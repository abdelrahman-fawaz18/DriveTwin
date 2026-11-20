import dataclasses
import json
import math

import pytest

from evdt.config import ROOT
from evdt.monitoring.contracts import monitoring_contract, read_receipt
from evdt.monitoring.plant import (
    DependentPlant,
    EnergyLedger,
    PlantConfig,
    battery_step_power,
    temperature_step,
)
from evdt.monitoring.sensors import SensorBank
from evdt.monitoring.spool import Identity, Spool


@pytest.fixture
def config():
    return PlantConfig.load(ROOT / "simulation/montreal/plant.json")


def state(plant, power=100000, speed=10, acceleration=1, dt=0.2, charging=0.0):
    return plant.step(
        pack_power_w=power,
        energy_j=1e9,
        capacity_j=1.26e9,
        speed_m_s=speed,
        acceleration_m_s2=acceleration,
        ambient_k=293.15,
        dt_s=dt,
        charging_power_w=charging,
    )


def test_power_current_soc_and_auxiliary_accounting(config):
    row = state(DependentPlant(config))
    assert row["battery.pack.current"] * row["battery.pack.voltage"] == pytest.approx(100000)
    assert row["battery.pack.soc"] == pytest.approx(1e9 / 1.26e9)
    assert row["auxiliary.power"] == 8000
    assert row["hvac.power"] == 5000  # subset, not an extra 5 kW drain
    assert row["traction.motor.torque"] * row["traction.motor.speed"] == pytest.approx(92000 * 0.9)


def test_regeneration_sign_motor_reconstruction_and_brake_proxy(config):
    row = state(DependentPlant(config), power=-50000, acceleration=-2)
    assert row["battery.pack.current"] < 0
    assert row["traction.motor.torque"] * row["traction.motor.speed"] == pytest.approx(
        -58000 / 0.65
    )
    assert row["braking.pressure"] == pytest.approx(1e6 * 2 / 4.5)
    assert state(DependentPlant(config), acceleration=-8)["braking.pressure"] == 1e6
    assert state(DependentPlant(config), speed=0)["traction.motor.torque"] == 0


def test_station_power_changes_net_pack_current_without_motor_regeneration(config):
    draw = config.auxiliary_power_w
    charging = 120000
    reference = state(DependentPlant(config), power=draw, speed=0)
    plugged_in = state(DependentPlant(config), power=draw - charging, speed=0, charging=charging)
    assert plugged_in["battery.pack.power"] == draw - charging
    assert plugged_in["battery.pack.current"] == pytest.approx((draw - charging) / config.voltage_v)
    for signal in (
        "traction.motor.torque",
        "traction.motor.temperature",
        "battery.pack.temperature",
    ):
        assert plugged_in[signal] == reference[signal]
    with pytest.raises(ValueError, match="Invalid bounded plant state"):
        state(DependentPlant(config), power=draw, speed=0, charging=-1)


def test_thermal_solution_composition_cooling_and_equilibrium():
    # Constant heat over 10 seconds must give the same answer with either step partition.
    one = temperature_step(300, 290, 2000, 10000, 100, 10)
    many = 300.0
    for _ in range(50):
        many = temperature_step(many, 290, 2000, 10000, 100, 0.2)
    assert one == pytest.approx(many, abs=1e-11)
    assert 290 < temperature_step(300, 290, 0, 10000, 100, 10) < 300
    assert temperature_step(310, 290, 2000, 10000, 100, 10) == pytest.approx(310)
    with pytest.raises(ValueError):
        temperature_step(300, 290, -1, 10000, 100, 0.2)


@pytest.mark.parametrize(
    "changes",
    [
        {"propulsion_efficiency": 1.01},
        {"recuperation_efficiency": 0},
        {"voltage_v": 0},
        {"wheel_radius_m": math.nan},
        {"pack_heat_fraction": 1.1},
        {"hvac_power_w": -1},
        {"model_version": "unknown"},
    ],
)
def test_bad_parameters_fail_before_simulation(config, changes):
    with pytest.raises(ValueError):
        dataclasses.replace(config, **changes).validate()


def test_nonfinite_state_cannot_be_published(config):
    with pytest.raises(ValueError):
        state(DependentPlant(config), power=math.inf)


@pytest.mark.parametrize(
    "key,value",
    [("front_area_m2", 0), ("rolling_drag_coefficient", -1), ("air_drag_coefficient", math.nan)],
)
def test_invalid_energy_geometry_is_rejected_before_sumo(tmp_path, key, value):
    raw = json.loads((ROOT / "simulation/montreal/plant.json").read_text())
    raw["energy"][key] = value
    path = tmp_path / "bad-plant.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        PlantConfig.load(path)


def test_energy_rate_conversion_and_boundary_ledger():
    ledger = EnergyLedger(3600, 7200)
    ledger.observe(2880, 3600, 0.2)  # 1 Wh/s = 3600 W; .2 s draws 720 J
    ledger.observe(3240, -1800, 0.2)
    assert ledger.net_consumed_j == pytest.approx(360)
    ledger.observe(7200, -36000, 0.2)  # regeneration beyond full capacity is clipped
    assert ledger.clipped_j < 0
    with pytest.raises(RuntimeError, match="mismatch"):
        ledger.observe(7100, 0, 0.2)


@pytest.mark.parametrize("step_wh,dt,power", [(1, 0.2, 18000), (-1, 0.2, -18000), (1, 1, 3600)])
def test_device_step_energy_to_power_has_explicit_interval(step_wh, dt, power):
    assert battery_step_power(step_wh, dt) == power
    with pytest.raises(ValueError):
        battery_step_power(step_wh, 0)


def test_catalog_cadence_is_driven_by_ticks_and_rejects_gaps():
    catalog = {
        "signals": {
            "a": {"minimum": 0, "maximum": 10, "unit": "1"},
            "b": {"minimum": 0, "maximum": 10, "unit": "1"},
        }
    }
    bank = SensorBank(catalog, 0.2, {"default_period_s": 1, "period_overrides_s": {"b": 0.4}})
    counts = {"a": 0, "b": 0}
    for tick in range(1, 11):
        for sample in bank.sample(tick, {"a": 1, "b": 2}):
            counts[sample["signal_id"]] += 1
    assert counts == {"a": 2, "b": 5}
    with pytest.raises(ValueError, match="coverage"):
        bank.sample(5, {"a": 1})
    with pytest.raises(ValueError, match="integral"):
        SensorBank(catalog, 0.2, {"default_period_s": 0.3, "period_overrides_s": {}})
    with pytest.raises(ValueError, match="Unregistered"):
        SensorBank(catalog, 0.2, {"default_period_s": 1, "period_overrides_s": {"missing": 1}})


def test_initial_20_match_plan_and_keep_extension_reserved():
    actual = set(monitoring_contract().catalog["signals"])
    planned = json.loads((ROOT / "benchmarks/workloads.json").read_text())
    assert actual == {s["signal_id"] for s in planned["signals"]}
    assert len(actual) == 20 and planned["held_back_extension"] not in actual


def test_current_queue_freezes_manifest_reference(tmp_path):
    import uuid

    contract = monitoring_contract()
    identity = Identity(str(uuid.uuid4()), "bus-001", "sumo-adapter")
    spool = Spool(
        tmp_path / "q.db",
        identity,
        contract.validate,
        read_receipt,
        schema_version="2.1.0",
        catalog_version="1.1.0",
    )
    spool.start_session()
    annotations = {"manifest_sha256": "a" * 64}
    event = spool.enqueue(
        [
            {
                "signal_id": "battery.pack.soc",
                "value": 0.8,
                "unit": "1",
                "quality": "good",
                "provenance": "simulated-observation",
            }
        ],
        1,
        annotations=annotations,
    )
    annotations["manifest_sha256"] = "b" * 64
    assert json.loads(spool.pending()[0].raw)["annotations"]["manifest_sha256"] == "a" * 64
    assert json.loads(event.raw)["schema_version"] == "2.1.0"
