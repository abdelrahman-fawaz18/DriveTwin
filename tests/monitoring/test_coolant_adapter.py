"""Analytic boundary response and real catalog/sampling extension semantics."""

import json
import math

import pytest

from evdt.config import ROOT
from evdt.monitoring.contracts import monitoring_contract
from evdt.monitoring.coolant_adapter import CoolantInlet
from evdt.monitoring.sensors import SensorBank


def config():
    return json.loads((ROOT / "simulation/montreal-bus-002/sensor-extension.json").read_text())


def test_inlet_exact_response_is_repeatable_and_separate_from_battery_state():
    c = config()
    a, b = CoolantInlet(c), CoolantInlet(c)
    ambient = 283.15
    target = ambient + c["ambient_offset_k"]
    for tick in range(1, 101):
        expected = target + (c["initial_k"] - target) * math.exp(-tick * 0.2 / c["response_time_s"])
        x = a.step({"ambient.temperature": ambient}, 0.2)
        assert x == b.step({"ambient.temperature": ambient}, 0.2)
        assert abs(x[a.signal_id] - expected) < 1e-10
        assert target < x[a.signal_id] < c["initial_k"]


def test_inlet_rejects_unknown_provider_nonfinite_and_invalid_step():
    for changed in (
        {"provider": "arbitrary-module"},
        {"response_time_s": 0},
        {"initial_k": float("nan")},
        {"ambient_offset_k": 1000},
    ):
        with pytest.raises(ValueError):
            CoolantInlet({**config(), **changed})
    with pytest.raises(ValueError):
        CoolantInlet(config()).step({"ambient.temperature": 283.15}, 0)


def test_new_catalog_cadence_and_quality_use_unchanged_generic_sampler():
    contract = monitoring_contract()
    new = contract.readers["1.3.0"].catalog
    assert "coolant.inlet.temperature" not in contract.readers["1.2.0"].catalog["signals"]
    plant = json.loads((ROOT / "simulation/montreal-bus-002/plant.json").read_text())
    bank = SensorBank(new, 0.2, plant["sampling"])
    readings = {
        name: (spec["minimum"] + spec["maximum"]) / 2 for name, spec in new["signals"].items()
    }
    assert not any(s["signal_id"] == "coolant.inlet.temperature" for s in bank.sample(5, readings))

    def at(tick):
        return next(
            s for s in bank.sample(tick, readings) if s["signal_id"] == "coolant.inlet.temperature"
        )

    assert at(10)["quality"] == "good"
    assert at(20)["quality"] == "missing" and at(20)["value"] is None
    assert at(40)["quality"] == "invalid" and at(40)["raw_value"] == 300
    assert bank.timings()["coolant.inlet.temperature"] == {"period_s": 2, "freshness_s": 6}
