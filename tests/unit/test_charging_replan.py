"""Station changes and passed visits request a new comparison once."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_station_state_key_tracks_only_reachable_bay_revisions(monkeypatch):
    pytest.importorskip("psycopg")
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "scripts/route_aware_energy")
    )
    from charging_idle import station_state_key
    from test_moving_charge import bay

    from evdt.energy.moving_charge_assignment import AssignedBay
    from evdt.energy.moving_charge_detour import Detour

    early = AssignedBay(replace(bay(), bay_id="early", start_offset_m=5, end_offset_m=20), 2)
    late = AssignedBay(replace(bay(), bay_id="late", start_offset_m=15, end_offset_m=30), 7)
    bays = (early, late)
    revisions = {"early": 2, "late": 2}

    def at(index, offset, states=revisions):
        return station_state_key(
            bays, states, SimpleNamespace(external_index=index, lane_offset_m=offset)
        )

    assert at(1, 10) == at(2, 19.9)
    assert at(2, 19.9) != at(2, 20)
    assert at(2, 20) == at(5, 25, {"early": 3, "late": 2})
    assert at(5, 25) != at(5, 25, {"early": 2, "late": 4})
    branch = replace(early, detour=Detour(2, 4, ("a", "bay", "c"), 1))
    assert station_state_key((branch,), revisions, SimpleNamespace(external_index=1)) != (
        station_state_key((branch,), revisions, SimpleNamespace(external_index=2))
    )


def test_completion_rechecks_station_freshness_without_extending_the_worker_lease(monkeypatch):
    pytest.importorskip("psycopg")
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "scripts/route_aware_energy")
    )
    from charging_idle import station_expiry

    from evdt.energy.moving_charge_station import STATUS_VERSION, StationStatus

    initial = StationStatus(
        STATUS_VERSION, "bay", "scenario", 2, 100, 100, 1, "available", 600, 1, 120_000
    )
    other = replace(initial, bay_id="backup")
    job = SimpleNamespace(
        offers=(SimpleNamespace(status=initial), SimpleNamespace(status=other)),
        evaluated_real_life_s=100,
        station_scenario_id="scenario",
        baseline=SimpleNamespace(
            policy=SimpleNamespace(worker_deadline_real_life_s=30, forecast_max_age_real_life_s=75)
        ),
        policy=SimpleNamespace(max_status_age_real_life_s=8),
    )
    current = replace(
        initial,
        observed_real_life_s=112,
        received_real_life_s=112.1,
        observed_working_s=13,
        available_from_working_s=13,
    )
    backup = replace(current, bay_id="backup", observed_real_life_s=111)
    decision = SimpleNamespace(preferred=None, fallback=None)
    assert station_expiry(job, (current, backup), 113, decision) == 119
    assert station_expiry(job, (initial, backup), 113, decision) is None
    assert station_expiry(job, (current,), 113, decision) is None
    assert station_expiry(job, (current, replace(backup, revision=3)), 113, decision) is None
    assert (
        station_expiry(job, (current, replace(backup, offered_power_w=80_000)), 113, decision)
        is None
    )
    assert (
        station_expiry(job, (current, replace(backup, received_real_life_s=114)), 113, decision)
        is None
    )
    assert station_expiry(job, (initial, backup), 99, decision) is None
    renewed = replace(
        current,
        observed_real_life_s=141,
        received_real_life_s=141,
        observed_working_s=42,
        available_from_working_s=42,
    )
    renewed_backup = replace(renewed, bay_id="backup")
    assert station_expiry(job, (renewed, renewed_backup), 142, decision) is None
    assert station_expiry(job, (renewed, renewed_backup), 142, decision, rechecking=True) == 149
    assert station_expiry(job, (renewed, renewed_backup), 176, decision, rechecking=True) is None
    assert (
        station_expiry(
            job, (replace(renewed, revision=3), renewed_backup), 142, decision, rechecking=True
        )
        is None
    )

    from test_moving_charge import bay

    offer = SimpleNamespace(stop=SimpleNamespace(bay=bay()), status=initial)
    candidate = SimpleNamespace(
        bay_id=initial.bay_id,
        approach=SimpleNamespace(
            forecast=SimpleNamespace(summary=SimpleNamespace(arrival_working_s=30))
        ),
        transfer=SimpleNamespace(start_working_s=60),
    )
    job.offers = (offer,)
    decision.preferred = candidate
    assert station_expiry(job, (current,), 113, decision) == 120
    candidate.approach.forecast.summary.arrival_working_s = 12
    assert station_expiry(job, (current,), 113, decision) is None


def test_fresh_infeasible_energy_result_satisfies_startup(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts/geographic"))
    from live_validation import startup_forecast

    result = {"status": "infeasible", "reason": "unserved_energy_or_power"}
    assert startup_forecast({"current": None, "diagnostic": {"payload": result}}) == result
    assert startup_forecast({"current": None, "diagnostic": None, "items": [result]}) is None
    assert startup_forecast({"current": {"status": "available"}}) == {"status": "available"}
