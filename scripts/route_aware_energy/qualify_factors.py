"""Small independent arithmetic comparisons; starts no bus or services."""

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from evdt.energy.route_aware.contracts import (  # noqa: E402
    Conditions,
    LiveState,
    RoadPoint,
    RouteContext,
    RouteOccurrence,
    SourceScope,
    VehicleProfile,
)
from evdt.energy.route_aware.geometry import remaining_route  # noqa: E402
from evdt.energy.route_aware.ledger import integrate  # noqa: E402
from evdt.energy.route_aware.motion import MotionPhase, MotionPlan  # noqa: E402
from evdt.energy.route_aware.policy import ForecastPolicy  # noqa: E402
from evdt.energy.route_aware.wire import decode_record, fingerprint  # noqa: E402


def run() -> dict:
    profile = decode_record(
        VehicleProfile,
        json.loads((ROOT / "qualification/energy/route-aware/vehicle-profile.json").read_text()),
    )
    state = LiveState(
        SourceScope("analytical", "bus", "reference", 1, "session"),
        "start",
        "a" * 64,
        0,
        100,
        100,
        "running",
        "straight",
        0,
        0,
        0.8,
        10,
        8000,
        "not_charging",
    )
    road = RouteOccurrence(
        0,
        "east",
        "east_0",
        (RoadPoint(0, 0, 0), RoadPoint(1000, 0, 0)),
        10,
        "synthetic",
        "surface",
        "declared_flat",
    )
    route = remaining_route(
        RouteContext(
            "b" * 64, "straight", "local-east-north-meters", "synthetic", profile.origin, (road,)
        ),
        state,
    )
    motion = MotionPlan((MotionPhase(0, "cruise", 0, 100, 1000, 10, 10, 0),), 1000, 100, 0, 100, ())
    baseline = Conditions(profile.origin, 90, 0, 1000, auxiliary_power_w=8000)
    cases = (
        ("baseline", baseline, profile, state),
        (
            "headwind_10_m_s",
            replace(baseline, wind_east_m_s=-10, wind_basis="synthetic"),
            profile,
            state,
        ),
        (
            "tailwind_5_m_s",
            replace(baseline, wind_east_m_s=5, wind_basis="synthetic"),
            profile,
            state,
        ),
        (
            "crosswind_10_m_s",
            replace(baseline, wind_north_m_s=10, wind_basis="synthetic"),
            profile,
            state,
        ),
        (
            "cold_dense_air",
            replace(baseline, ambient_temperature_k=250, pressure_pa=100_000),
            profile,
            state,
        ),
        (
            "additional_1000_kg",
            replace(baseline, loaded_mass_kg=profile.loaded_mass_kg + 1000),
            profile,
            state,
        ),
        ("declared_rolling_1_2", replace(baseline, rolling_multiplier=1.2), profile, state),
        ("auxiliary_12000_W", baseline, profile, replace(state, auxiliary_power_w=12_000)),
        ("propulsion_efficiency_0_8", baseline, replace(profile, propulsion_efficiency=0.8), state),
    )
    rows = []
    for name, condition, vehicle, current in cases:
        start = time.perf_counter()
        result = integrate(route, motion, current, vehicle, (condition,), ForecastPolicy())
        elapsed = time.perf_counter() - start
        mass = condition.loaded_mass_kg or vehicle.loaded_mass_kg
        density = (
            condition.pressure_pa / (287.05 * condition.ambient_temperature_k)
            if condition.pressure_pa and condition.ambient_temperature_k
            else vehicle.air_density_kg_m3
        )
        rolling = (
            vehicle.rolling_coefficient
            * condition.rolling_multiplier
            * mass
            * vehicle.gravity_m_s2
            * 1000
        )
        air = (
            0.5
            * density
            * vehicle.drag_coefficient
            * vehicle.frontal_area_m2
            * math.hypot(10 - condition.wind_east_m_s, condition.wind_north_m_s)
            * (10 - condition.wind_east_m_s)
            * 1000
        )
        reference = (
            rolling + air
        ) / vehicle.propulsion_efficiency + current.auxiliary_power_w * 100
        rows.append(
            {
                "case": name,
                "ledger": asdict(result),
                "expected_net_j": reference,
                "error_j": result.net_battery_j - reference,
                "real_life_seconds": elapsed,
                "input_sha256": fingerprint((route, motion, current, vehicle, condition)),
                "passed": abs(result.net_battery_j - reference) < 1e-5 and result.motion_feasible,
            }
        )
    source = {
        p.relative_to(ROOT).as_posix(): __import__("hashlib").sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT / "src/evdt/energy/route_aware").glob("*.py"))
    }
    return {
        "version": "route-aware-factor-arithmetic/1.0.0",
        "passed": all(row["passed"] for row in rows),
        "scope": "constant-speed closed-form sensitivity; no whole-trip accuracy claim",
        "source_sha256": source,
        "cases": rows,
        "bus_started": False,
        "containers_started": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT / "evidence/energy-v2") or output.exists():
        raise ValueError("Choose a new report below evidence/energy-v2")
    result = run()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Independent factor arithmetic passed: {result['passed']}; {output.relative_to(ROOT)}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
