"""Verify measured accessory accounting and guarded release at the queued bay."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from evdt.energy.moving_charge_scenario import BayInterval, BaySchedule, StationScenario
from evdt.monitoring.charging_queue import apply_waiting_equipment
from evdt.monitoring.moving_charge_executor import MovingChargeExecutor


def test_waiting_equipment_updates_the_single_battery_and_accounts_for_existing_draw():
    cell = {"energy_wh": 500.0}
    vehicle = Mock()
    vehicle.getParameter.side_effect = lambda *_: str(cell["energy_wh"])
    vehicle.setParameter.side_effect = lambda _asset, _name, value: cell.update(
        energy_wh=float(value)
    )
    energy, draw = apply_waiting_equipment(
        vehicle,
        "bus",
        energy_j=1_800_000,
        native_draw_wh=1.0,
        equipment_w=600,
        step_s=30,
    )
    assert energy == pytest.approx(1_785_600)
    assert draw == pytest.approx(5.0)
    vehicle.setParameter.assert_called_once()
    assert apply_waiting_equipment(
        vehicle,
        "bus",
        energy_j=1_785_600,
        native_draw_wh=6,
        equipment_w=600,
        step_s=30,
    ) == (1_785_600, 6)
    with pytest.raises(RuntimeError, match="cannot supply"):
        apply_waiting_equipment(
            vehicle, "bus", energy_j=3000, native_draw_wh=0, equipment_w=600, step_s=30
        )


@pytest.mark.parametrize(
    ("initial_power", "charge_wh", "at_bay", "expected_reason"),
    [
        (0, 130, True, None),
        (120_000, 130, True, "plant_power_changed"),
        (0, 100, True, "queue_reserve_at_risk"),
        (0, 130, False, "queue_position_changed"),
    ],
)
def test_queue_release_requires_safe_wait_and_valid_slot_through_setup(
    initial_power, charge_wh, at_bay, expected_reason
):
    executor = MovingChargeExecutor.__new__(MovingChargeExecutor)
    executor.asset = "bus"
    bay = SimpleNamespace(
        bay_id="bay",
        power_w=120_000,
        setup_working_s=2,
        lane_id="lane",
        start_offset_m=5,
        end_offset_m=10,
    )
    schedule = BaySchedule(
        "bay",
        (BayInterval(0, 30, "occupied", None), BayInterval(30, 100, "available", bay.power_w)),
    )
    executor.assignment = SimpleNamespace(
        scenario=StationScenario("scenario", "run", (schedule,)),
        policy=SimpleNamespace(equipment_w=1200),
    )
    executor.active = bay, 0.14
    executor.events = []
    executor.interrupted = executor.target_reached = executor.queue_power_ready = False
    executor.queue_opens_working_s = 30
    executor.queue_arrival_working_s = 24
    executor.queue_stop_deadline_s = executor.station_deadline_s = 100
    cell = {"power": initial_power}
    station = Mock()
    station.getChargingPower.side_effect = lambda _bay: cell["power"]
    station.setChargingPower.side_effect = lambda _bay, power: cell.update(power=power)
    vehicle = Mock()
    vehicle.getStops.return_value = (SimpleNamespace(stoppingPlaceID="bay"),)
    vehicle.isStopped.return_value = at_bay
    vehicle.getSpeed.return_value = 0
    vehicle.getLaneID.return_value = "lane"
    vehicle.getLanePosition.return_value = 8
    vehicle.getParameter.side_effect = lambda _asset, parameter: str(
        1000 if parameter == "device.battery.capacity" else charge_wh
    )
    connection = SimpleNamespace(chargingstation=station, vehicle=vehicle)
    assert executor.before_step(connection, 29.6, 29.8) is (expected_reason is None)
    if expected_reason is not None:
        assert executor.interrupted
        assert executor.events[0]["reason"] == expected_reason
        station.setChargingPower.assert_not_called()
        return
    assert executor.before_step(connection, 31.6, 31.8)
    station.setChargingPower.assert_not_called()
    assert executor.before_step(connection, 31.8, 32)
    station.setChargingPower.assert_called_once_with("bay", 120_000)
    assert executor.queue_power_ready
    assert executor.before_step(connection, 32, 32.2)
    assert not executor.before_step(connection, 99.8, 100)
