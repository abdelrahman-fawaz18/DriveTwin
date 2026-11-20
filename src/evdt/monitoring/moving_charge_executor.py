"""Apply one granted on-route charging stop and record the simulator response."""

import json
import time
from copy import copy
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID

from evdt.energy.moving_charge_assignment import StationAssignment
from evdt.energy.moving_charge_recovery import (
    RECOVERY_HOLD_REAL_LIFE_S,
    HeldChargeStop,
    HeldDepartureInputs,
    ReplacementOf,
)
from evdt.energy.route_aware.context import SourceContext, SourcePlan, SourceStop
from evdt.energy.route_aware.validation import number
from evdt.energy.route_aware.wire import decode_record, fingerprint
from evdt.monitoring.charging_queue import apply_waiting_equipment, waiting_at_bay
from evdt.monitoring.charging_stop import (
    CHARGING_STATION,
    MOVING_CHARGE_STOP_LIMIT_S,
    POSITION_TOLERANCE_M,
    StopInsertionRefused,
    admitted_charge_wh,
    insert_on_route_charge,
    stop_identity,
)

RESERVE_SOC = 0.1


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MovingChargeExecutor:
    def __init__(self, run, asset, assignment_path, credential_path):
        self.run, self.asset = run, asset
        path = Path(assignment_path)
        if path.stat().st_size > 512 * 1024:
            raise ValueError("Moving charging assignment exceeds its bound")
        self.assignment = decode_record(StationAssignment, json.loads(path.read_bytes()))
        if (self.assignment.run_id, self.assignment.asset_id) != (run, asset):
            raise ValueError("Moving charging executor assignment is outside its scope")
        self.assignment_sha256 = fingerprint(self.assignment)
        self.scenario_sha256 = self.assignment.scenario.sha256
        self.token = Path(credential_path).read_text().strip()
        if len(self.token) < 32:
            raise ValueError("Moving charging executor credential is unavailable")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.active = None
        self.finished = False
        self.attempted = None
        self.command_id = None
        self.events = []
        self.charged_wh = 0.0
        self.original_delivered_wh = 0.0
        self.replacement_command_id = None
        self.minimum_soc = 1.0
        self.target_reached = False
        self.station_deadline_s = None
        self.effect_state = None
        self.effect_working_s = None
        self.interrupted = False
        self.interruption_snapshot = None
        self.queue_opens_working_s = None
        self.queue_arrival_working_s = None
        self.queue_power_ready = False
        self.queue_equipment_j = 0.0
        self.queue_stop_deadline_s = None
        self.queue_trial = False

    def held_observation(self, scope, manifest_sha256, *, auxiliary_power_w, battery_temperature_k):
        snapshot = self.interruption_snapshot
        if (
            self.command_id is None
            or snapshot is None
            or snapshot.get("status") != "held_at_committed_bay"
            or snapshot["route_sha256"] != fingerprint(self.assignment.baseline_edges)
            or snapshot["power_w"] != 0
        ):
            return None
        plan = SourcePlan(
            self.assignment.network_sha256,
            self.assignment.baseline_edges,
            tuple(
                SourceStop(
                    stop["stop_id"],
                    stop["external_index"],
                    stop["lane_id"],
                    stop["offset_m"],
                    stop["dwell_working_s"],
                )
                for stop in snapshot["remaining_passenger_stops"]
            ),
        )
        return HeldChargeStop(
            scope,
            self.command_id,
            manifest_sha256,
            self.assignment_sha256,
            plan.revision,
            snapshot["remaining_stops_sha256"],
            snapshot["bay_id"],
            snapshot["external_index"],
            snapshot["lane_id"],
            snapshot["lane_offset_m"],
            snapshot["working_s"],
            snapshot["energy_j"] / snapshot["capacity_j"],
            time.time(),
            HeldDepartureInputs(auxiliary_power_w, battery_temperature_k, plan.remaining_stops),
        )

    def recover(self, connection, journey, held, publish_clock):
        """Keep SUMO stopped until a separately approved, fresh grant is reconciled."""
        from evdt.command_timing import host_clock
        from evdt.contract_profile import Rejected
        from evdt.moving_charge_journal import decode

        link = ReplacementOf(held.interrupted_command_id, fingerprint(held))
        request = {"run_id": self.run, "asset_id": self.asset}
        if (
            self.active is None
            or self.command_id != held.interrupted_command_id
            or self.replacement_command_id is not None
        ):
            return False
        held_bay = self.active[0]
        started = time.monotonic()
        heartbeat = started
        try:
            recorded = self.call(
                "recovery/interrupt",
                {
                    **request,
                    "command_id": link.command_id,
                    "observation_sha256": link.evidence_sha256,
                },
            )
            if not recorded or recorded.get("observation_sha256") != link.evidence_sha256:
                return False
            while time.monotonic() - started < RECOVERY_HOLD_REAL_LIFE_S:
                if time.monotonic() - heartbeat >= 0.75:
                    publish_clock("paused", held.working_s)
                    heartbeat = time.monotonic()
                active = self.call("recovery/pending")
                identity = active and active.get("command_id")
                if identity is None:
                    time.sleep(0.2)
                    continue
                if str(UUID(identity)) != identity:
                    return False
                request["command_id"] = identity
                offer = self.call("recovery/grant", {**request, "sim_s": held.working_s})
                if offer is None:
                    time.sleep(0.2)
                    continue
                reviewed = decode(offer)
                clock = host_clock()
                if (
                    not 0
                    <= clock.utc.timestamp() - held.observed_real_life_s
                    < RECOVERY_HOLD_REAL_LIFE_S
                ):
                    raise Rejected("held_charge_source_unavailable")
                reviewed.lease.verify(clock)
                self.apply_replacement(connection, journey, held, held_bay, link, reviewed)
                break
            else:
                return False
        except StopInsertionRefused:
            self.acknowledge(request, "refused", action="recovery/ack")
            return False
        except (OSError, ValueError):
            # If a reply was lost, it can no longer be used to mutate SUMO.
            # A known refusal and an interrupted handoff also retain the hold.
            if "command_id" in request:
                self.acknowledge(request, "unknown", action="recovery/ack")
            return False
        except Exception:
            if "command_id" in request:
                self.acknowledge(request, "unknown", action="recovery/ack")
            raise
        self.replacement_command_id = identity
        if not self.acknowledge(request, "applied", action="recovery/ack"):
            self.interrupted = True
            return False
        return True

    def apply_replacement(self, connection, journey, held, held_bay, link, reviewed):
        from evdt.moving_charge_recovery_journal import verify_replacement

        verify_replacement(held, reviewed)
        if reviewed.replacement_of != link:
            raise StopInsertionRefused("Replacement grant references a different hold")
        vehicle = connection.vehicle
        if (
            connection.simulation.getTime() != held.working_s
            or vehicle.getLaneID(self.asset) != held.lane_id
            or vehicle.getRouteIndex(self.asset) != held.external_index
            or vehicle.getLanePosition(self.asset) != held.lane_offset_m
            or tuple(vehicle.getRoute(self.asset)) != self.assignment.baseline_edges
            or fingerprint(tuple(map(stop_identity, vehicle.getStops(self.asset)[1:])))
            != held.remaining_stops_sha256
            or abs(
                float(vehicle.getParameter(self.asset, "device.battery.chargeLevel"))
                / float(vehicle.getParameter(self.asset, "device.battery.capacity"))
                - held.soc
            )
            > 1e-10
        ):
            raise StopInsertionRefused("Live position, itinerary or battery has changed")
        entry = next(
            (bay for bay in self.assignment.bays if bay.bay.bay_id == reviewed.bay_id), None
        )
        if (
            entry is None
            or entry.detour is not None
            or entry.external_index != reviewed.bay_external_index
        ):
            raise StopInsertionRefused("Replacement is outside the prepared route")
        now = time.time()
        status = next(
            item
            for item in self.assignment.scenario.observe(
                run_id=self.run,
                working_s=held.working_s,
                observed_real_life_s=now,
                received_real_life_s=now,
                expected_sha256=self.scenario_sha256,
            )
            if item.bay_id == reviewed.bay_id
        )
        if (
            reviewed.station_scenario_id != status.scenario_id
            or reviewed.station_revision != status.revision
            or status.state != "available"
            or status.offered_power_w != entry.bay.power_w
            or status.open_until_working_s < held.working_s + MOVING_CHARGE_STOP_LIMIT_S
        ):
            raise StopInsertionRefused("Replacement supply is outside the approved handoff domain")
        insert_on_route_charge(
            connection,
            self.asset,
            journey,
            entry,
            stop_limit_s=MOVING_CHARGE_STOP_LIMIT_S,
            minimum_lead_m=25,
            braking_m_s2=1.5,
            held_bay=held_bay,
        )
        journey.charge_stop = entry
        self.active = (entry.bay, reviewed.target_soc)
        self.interrupted = False
        self.station_deadline_s = status.open_until_working_s
        self.queue_opens_working_s = None
        self.effect_state = self.effect_working_s = None
        self.original_delivered_wh = self.charged_wh
        self.charged_wh = 0.0
        self.bay_id = entry.bay.bay_id
        self.events.append(
            {"event": "replacement_inserted", "bay_id": self.bay_id, "working_s": held.working_s}
        )

    def remaining_charge_stop(self, vehicle, bay):
        matches = [
            stop for stop in vehicle.getStops(self.asset) if stop.stoppingPlaceID == bay.bay_id
        ]
        if len(matches) != 1 or matches[0].lane != bay.lane_id:
            raise RuntimeError("Scheduled charging stop changed before the departure target")

    def call(self, action, value=None):
        raw = None if value is None else json.dumps(value, allow_nan=False).encode()
        request = Request(
            "http://command-api:8000/internal/v2/moving-charge/" + action,
            raw,
            {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(request, timeout=0.4) as response:
                content = response.read(8193)
                if len(content) > 8192:
                    raise ValueError("Oversized moving charging response")
                return json.loads(content)
        except HTTPError as exc:
            with exc:
                if exc.code in (409, 422):
                    if action == "grant":
                        reason = json.loads(exc.read(2048)).get("detail")
                        if isinstance(reason, str) and reason.startswith("moving_charge_"):
                            self.events.append({"event": "grant_refused", "reason": reason})
                    return None
            raise

    def try_apply(self, connection, journey, context: SourceContext, soc, sim_s):
        from evdt.command_timing import host_clock
        from evdt.contract_profile import Rejected
        from evdt.energy.moving_charge_handoff import revalidate
        from evdt.moving_charge_journal import decode

        if self.finished or self.active or context is None:
            return
        vehicle = connection.vehicle
        if (
            vehicle.isStopped(self.asset)
            or vehicle.getSpeed(self.asset) <= 0
            or vehicle.getLaneID(self.asset).startswith(":")
            or soc < 0.1
        ):
            return
        try:
            status = self.call("pending")
            command_id = status and status.get("command_id")
            if (
                command_id is None
                or str(UUID(command_id)) != command_id
                or command_id == self.attempted
            ):
                return
            request = {"run_id": self.run, "asset_id": self.asset, "command_id": command_id}
            raw = self.call("grant", {**request, "sim_s": sim_s})
            if raw is None:
                return
            self.attempted = command_id
            reviewed = decode(raw)
        except (OSError, ValueError):
            # The authority returns a grant once. A timeout never supplies
            # instructions, and a repeated post can only read a refusal.
            return
        try:
            clock = host_clock()
            if (
                (reviewed.scope.run_id, reviewed.scope.asset_id) != (self.run, self.asset)
                or vehicle.getLaneID(self.asset) != context.lane_id
                or vehicle.getRouteIndex(self.asset) != context.external_index
                or tuple(vehicle.getRoute(self.asset)) != self.assignment.baseline_edges
            ):
                raise Rejected("Local vehicle movement superseded the charging grant")
            entry = next(
                (row for row in self.assignment.bays if row.bay.bay_id == reviewed.bay_id), None
            )
            if entry is None or entry.external_index != reviewed.bay_external_index:
                raise Rejected("Local station assignment differs from the granted stop")
            stations = self.assignment.scenario.observe(
                run_id=self.run,
                working_s=context.working_s,
                observed_real_life_s=context.observed_real_life_s,
                received_real_life_s=clock.utc.timestamp(),
                expected_sha256=self.scenario_sha256,
            )
            station = next(row for row in stations if row.bay_id == reviewed.bay_id)
            if reviewed.queue_opens_working_s is not None and not self.queue_trial:
                raise Rejected("Queued charging is outside this source profile")
            if station.offered_power_w != entry.bay.power_w:
                raise Rejected("This executor requires the configured station power")
            if station.open_until_working_s < sim_s + MOVING_CHARGE_STOP_LIMIT_S:
                raise Rejected("Station schedule cannot cover this bounded stop")
            revalidate(
                reviewed,
                context,
                station,
                clock=clock,
                working_s=sim_s,
                assignment_sha256=self.assignment_sha256,
                job_fence=reviewed.job_fence,
                candidate_sha256=reviewed.candidate_sha256,
                source_state="running",
                reserve_soc=RESERVE_SOC,
                soc=soc,
            )
            if entry.detour is not None:
                entry, plan = self.apply_detour(connection, journey, context, entry, reviewed)
            else:
                if reviewed.detour_sha256 is not None:
                    raise Rejected("Local station has no reviewed detour")
                plan = journey
            if (
                reviewed.queue_opens_working_s is not None
                and connection.chargingstation.getChargingPower(entry.bay.bay_id)
                != entry.bay.power_w
            ):
                raise Rejected("Queued charger power changed before stop insertion")
            insert_on_route_charge(
                connection,
                self.asset,
                plan,
                entry,
                stop_limit_s=MOVING_CHARGE_STOP_LIMIT_S,
                minimum_lead_m=25,
                braking_m_s2=1.5,
            )
            if reviewed.queue_opens_working_s is not None:
                station_device = connection.chargingstation
                station_device.setChargingPower(entry.bay.bay_id, 0)
                station_device.setChargeDelay(entry.bay.bay_id, 0)
                if (
                    station_device.getChargingPower(entry.bay.bay_id) != 0
                    or station_device.getChargeDelay(entry.bay.bay_id) != 0
                ):
                    raise RuntimeError("Could not initialize the queued charger")
        except (Rejected, StopInsertionRefused):
            if tuple(vehicle.getRoute(self.asset)) != self.assignment.baseline_edges:
                self.acknowledge(request, "unknown")
                raise RuntimeError("Charging route changed before stop insertion was verified")
            self.acknowledge(request, "refused")
            self.finished = True
        except Exception:
            self.acknowledge(request, "unknown")
            raise
        else:
            journey.spec = plan.spec
            journey.charge_stop = entry
            self.active = (entry.bay, reviewed.target_soc)
            self.minimum_soc = soc
            self.command_id = command_id
            self.bay_id = entry.bay.bay_id
            self.station_deadline_s = station.open_until_working_s
            self.queue_opens_working_s = reviewed.queue_opens_working_s
            self.events.append(
                {
                    "event": "stop_inserted",
                    "working_s": sim_s,
                    "bay_id": entry.bay.bay_id,
                    **(
                        {
                            "detour_sha256": reviewed.detour_sha256,
                            "observed_route_sha256": fingerprint(tuple(plan.spec["route_edges"])),
                        }
                        if reviewed.detour_sha256 is not None
                        else {}
                    ),
                }
            )
            self.acknowledge(request, "applied")

    def apply_detour(self, connection, journey, context, entry, reviewed):
        from evdt.contract_profile import Rejected

        detour = entry.detour
        if fingerprint(detour) != reviewed.detour_sha256:
            raise Rejected("Local detour differs from the operator grant")
        try:
            replacement = copy(journey)
            replacement.spec = journey.detour_plan(detour)
        except ValueError as exc:
            raise Rejected("Local detour does not preserve the reviewed itinerary") from exc
        vehicle = connection.vehicle
        route = tuple(replacement.spec["route_edges"])
        entry = replace(entry, external_index=entry.detour_bay_index, detour=None)
        before = vehicle.getStops(self.asset)
        stop_indices = replacement.stop_occurrences(route, before)
        lane, target = entry.bay.lane_id, route[entry.external_index]
        if (
            context.external_index >= detour.branch_index
            or route[context.external_index] != connection.lane.getEdgeID(context.lane_id)
            or connection.lane.getEdgeID(lane) != target
            or route[context.external_index :].count(target) != 1
            or entry.external_index in stop_indices
        ):
            raise Rejected("Local vehicle position is outside the reviewed detour domain")
        vehicle.setRoute(self.asset, route[context.external_index :])
        if (
            tuple(vehicle.getRoute(self.asset)) != route
            or vehicle.getRouteIndex(self.asset) != context.external_index
            or tuple(map(stop_identity, vehicle.getStops(self.asset)))
            != tuple(map(stop_identity, before))
        ):
            raise RuntimeError("Charging detour could not be reconciled before the next SUMO step")
        return entry, replacement

    def observe(self, connection, sim_s, soc):
        if self.command_id is not None:
            self.minimum_soc = min(self.minimum_soc, soc)
            if soc < RESERVE_SOC:
                raise RuntimeError("Charge fell below reserve after the charging handoff")
        if self.active is None:
            return 0.0
        vehicle = connection.vehicle
        bay, target = self.active
        transferred = admitted_charge_wh(vehicle, self.asset, bay)
        if self.queue_opens_working_s is not None and not self.queue_power_ready and transferred:
            raise RuntimeError("Queued charger transferred before the reviewed opening and setup")
        if transferred:
            if self.charged_wh == 0:
                self.effect_state = "charging"
                self.effect_working_s = sim_s
                self.events.append(
                    {
                        "event": "charging_observed",
                        "working_s": sim_s,
                        "bay_id": bay.bay_id,
                        "soc": soc,
                    }
                )
            self.charged_wh += transferred
            if soc >= target and not self.target_reached:
                self.target_reached = True
                self.effect_state = "target_reached"
                self.effect_working_s = sim_s
                self.events.append({"event": "target_observed", "working_s": sim_s, "soc": soc})
                vehicle.resume(self.asset)
        elif self.target_reached:
            if vehicle.getSpeed(self.asset) > 0.1:
                self.effect_state = "motion_resumed"
                self.effect_working_s = sim_s
                self.events.append({"event": "motion_resumed", "working_s": sim_s, "soc": soc})
                self.active = None
                self.finished = True
        else:
            if self.effect_state is None or vehicle.getSpeed(self.asset) > 0.1:
                self.remaining_charge_stop(vehicle, bay)
            if self.effect_state is None:
                # Read the stop back after a completed step before reporting an approach.
                self.effect_state = "approaching"
                self.effect_working_s = sim_s
        return transferred

    def account_queued_step(self, connection, sim_s, energy_j, native_draw_wh, step_s):
        if self.queue_opens_working_s is None or self.queue_power_ready or self.active is None:
            return energy_j, native_draw_wh
        bay, _ = self.active
        vehicle = connection.vehicle
        at_bay = waiting_at_bay(vehicle, self.asset, bay)
        if self.queue_arrival_working_s is None:
            if at_bay:
                self.queue_arrival_working_s = self.effect_working_s = sim_s
                self.queue_stop_deadline_s = sim_s + MOVING_CHARGE_STOP_LIMIT_S - step_s
                self.effect_state = "waiting_for_charger"
                self.events.append(
                    {"event": "queue_arrival", "working_s": sim_s, "bay_id": bay.bay_id}
                )
            return energy_j, native_draw_wh
        if not at_bay:
            raise RuntimeError("Bus left the queued bay before charging began")
        observed_j, draw_wh = apply_waiting_equipment(
            vehicle,
            self.asset,
            energy_j=energy_j,
            native_draw_wh=native_draw_wh,
            equipment_w=self.assignment.policy.equipment_w,
            step_s=step_s,
        )
        self.queue_equipment_j += (draw_wh - native_draw_wh) * 3600
        return observed_j, draw_wh

    def before_step(self, connection, completed_working_s, next_working_s):
        """Return whether the next step may advance after checking the selected bay."""
        if self.interrupted:
            return False
        if self.active is None or self.target_reached:
            return True
        bay, _ = self.active
        interval = self.assignment.scenario.active_interval(bay.bay_id, next_working_s)
        power = (
            min(bay.power_w, interval.power_w)
            if interval is not None and interval.state == "available"
            else 0.0
        )
        reason = None
        current_power = connection.chargingstation.getChargingPower(bay.bay_id)
        if self.queue_stop_deadline_s is not None and next_working_s >= self.queue_stop_deadline_s:
            reason = "approved_window_ended"
        elif self.queue_opens_working_s is not None and not self.queue_power_ready:
            opening = self.queue_opens_working_s
            waiting = next_working_s < opening
            expected_state = "occupied" if waiting else "available"
            if next_working_s >= self.station_deadline_s:
                reason = "approved_window_ended"
            elif (
                interval is None
                or interval.state != expected_state
                or (not waiting and power != bay.power_w)
            ):
                reason = "station_unavailable"
            elif current_power != 0:
                reason = "plant_power_changed"
            else:
                arrival = self.queue_arrival_working_s
                if arrival is None:
                    return True
                vehicle = connection.vehicle
                if not waiting_at_bay(vehicle, self.asset, bay):
                    reason = "queue_position_changed"
                elif next_working_s + 1e-9 >= max(arrival, opening) + bay.setup_working_s:
                    station = connection.chargingstation
                    station.setChargingPower(bay.bay_id, bay.power_w)
                    if station.getChargingPower(bay.bay_id) != bay.power_w:
                        raise RuntimeError("Queued charger did not restore reviewed power")
                    self.queue_power_ready = True
                    self.events.append(
                        {
                            "event": "queue_power_ready",
                            "working_s": next_working_s,
                            "bay_id": bay.bay_id,
                        }
                    )
                    return True
                else:
                    capacity_wh = number(
                        float(vehicle.getParameter(self.asset, "device.battery.capacity")),
                        "queued battery capacity",
                        0.01,
                        1e7,
                    )
                    charge_wh = number(
                        float(vehicle.getParameter(self.asset, "device.battery.chargeLevel")),
                        "queued battery charge",
                        0,
                        capacity_wh,
                    )
                    draw_wh = (
                        self.assignment.policy.equipment_w
                        * (next_working_s - completed_working_s)
                        / 3600
                    )
                    if charge_wh - draw_wh < capacity_wh * RESERVE_SOC:
                        reason = "queue_reserve_at_risk"
        elif power == 0:
            reason = "station_unavailable"
        elif next_working_s >= self.station_deadline_s:
            reason = "approved_window_ended"
        elif current_power != power:
            reason = "plant_power_changed"
        if reason is not None:
            # Retain the last completed step and require a new reviewed run.
            self.interrupted = True
            self.events.append(
                {
                    "event": "charge_interrupted",
                    "working_s": completed_working_s,
                    "bay_id": bay.bay_id,
                    "reason": reason,
                }
            )
        return not self.interrupted

    def capture_interruption(self, connection, journey, capacity_j):
        """Read back the held plant before considering a replacement stop."""
        if not self.interrupted or self.active is None:
            return
        bay, _ = self.active
        vehicle = connection.vehicle
        route = tuple(vehicle.getRoute(self.asset))
        stops = tuple(vehicle.getStops(self.asset))
        try:
            if journey.charge_stop is None or journey.charge_stop.bay != bay:
                raise ValueError("assigned_charging_stop_changed")
            indices = journey.stop_occurrences(route, stops)
            planned = journey.spec["stops"]
            if (
                len(stops) < 2
                or indices[-1] != journey.spec["endpoint"]["external_index"]
                or any(stop.stopFlags & CHARGING_STATION for stop in stops[1:])
            ):
                raise ValueError("remaining_passenger_stops_unverified")
            index = vehicle.getRouteIndex(self.asset)
            lane = vehicle.getLaneID(self.asset)
            offset = number(vehicle.getLanePosition(self.asset), "interruption offset", 0, 1e7)
            speed = number(vehicle.getSpeed(self.asset), "interruption speed", 0, 100)
            current_bay_is_scheduled = (
                stops[0].stoppingPlaceID == bay.bay_id
                and bool(stops[0].stopFlags & CHARGING_STATION)
                and indices[0] == index == journey.charge_stop.external_index
                and lane == bay.lane_id
                and bay.start_offset_m <= offset <= bay.end_offset_m + POSITION_TOLERANCE_M
            )
            if not (current_bay_is_scheduled and vehicle.isStopped(self.asset) and speed == 0):
                raise ValueError("vehicle_not_held_at_committed_bay")
            # The existing itinerary check above identifies the exact passenger suffix.
            # The last entry is the terminal hold rather than a passenger stop.
            passengers = tuple(planned[len(planned) - len(stops) + 1 : -1])
            working = number(connection.simulation.getTime(), "interruption time", 0, 1e12)
            if abs(working - self.events[-1]["working_s"]) > 1e-6:
                raise ValueError("simulation_advanced_after_hold")
            energy_j = number(
                float(vehicle.getParameter(self.asset, "device.battery.chargeLevel")) * 3600,
                "interruption battery energy",
                0,
                capacity_j,
            )
            power = number(
                connection.chargingstation.getChargingPower(bay.bay_id),
                "observed bay power",
                0,
                1e6,
            )
        except ValueError as exc:
            self.interruption_snapshot = {"status": "unreconciled", "reason": str(exc)}
            return
        self.interruption_snapshot = {
            "status": "held_at_committed_bay",
            "command_id": self.command_id,
            **(
                {"replacement_command_id": self.replacement_command_id}
                if getattr(self, "replacement_command_id", None) is not None
                else {}
            ),
            "bay_id": bay.bay_id,
            "working_s": working,
            "external_index": index,
            "lane_id": lane,
            "lane_offset_m": offset,
            "speed_m_s": speed,
            "capacity_j": capacity_j,
            "energy_j": energy_j,
            "delivered_j": self.charged_wh * 3600,
            "power_w": power,
            "route_sha256": fingerprint(route),
            "remaining_passenger_stops": passengers,
            "remaining_stops_sha256": fingerprint(tuple(map(stop_identity, stops[1:]))),
        }

    def observation(self):
        """Publish only effects measured in completed SUMO steps."""
        if self.effect_state is None:
            return {}
        return {
            "charging_effect": self.effect_state,
            "charging_bay": self.bay_id,
            "charging_effect_time": format(self.effect_working_s, ".12g"),
            "charging_delivered_j": format(self.charged_wh * 3600, ".12g"),
        }

    def report(self):
        replacement = getattr(self, "replacement_command_id", None)
        return {
            "command_id": self.command_id,
            "events": self.events,
            "delivered_j": (self.charged_wh + getattr(self, "original_delivered_wh", 0.0)) * 3600,
            **({"replacement_command_id": replacement} if replacement is not None else {}),
            "interrupted": self.interrupted,
            **(
                {"queue_equipment_j": self.queue_equipment_j}
                if self.queue_opens_working_s is not None
                else {}
            ),
            **(
                {"interruption_snapshot": self.interruption_snapshot}
                if self.interruption_snapshot is not None
                else {}
            ),
            "minimum_soc": self.minimum_soc if self.command_id is not None else None,
        }

    def acknowledge(self, request, state, *, action="ack"):
        try:
            result = self.call(action, {**request, "status": state})
            if result is None or result.get("state") != state:
                raise ValueError("Acknowledgment did not match the reported outcome")
            return True
        except (OSError, ValueError):
            self.events.append({"event": "acknowledgment_unavailable", "status": state})
            return False
