"""Compare forecast input factors against retained independent SUMO trips."""

import argparse
import hashlib
import json
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / name) for name in ("src", "scripts/route_aware_energy")]

from study import candidate, read, score  # noqa: E402

from evdt.energy.route_aware.forecast import forecast  # noqa: E402
from evdt.energy.route_aware.wire import ForecastRequest, decode_record, fingerprint  # noqa: E402


def alternatives(request: ForecastRequest):
    yield "full", request
    if any(
        point.elevation_m != 0
        for occurrence in request.route.occurrences
        for point in occurrence.shape
    ):
        flat = tuple(
            replace(road, shape=tuple(replace(point, elevation_m=0) for point in road.shape))
            for road in request.route.occurrences
        )
        yield "flat_grade", replace(request, route=replace(request.route, occurrences=flat))
    if request.vehicle.loaded_mass_kg != 18_000:
        yield (
            "fixed_18t_mass",
            replace(request, vehicle=replace(request.vehicle, loaded_mass_kg=18_000)),
        )
    if request.state.auxiliary_power_w != 8000:
        yield (
            "fixed_8kw_equipment",
            replace(
                request,
                state=replace(request.state, auxiliary_power_w=8000),
                conditions=tuple(
                    replace(row, auxiliary_power_w=8000) for row in request.conditions
                ),
            ),
        )


def summarize(rows):
    available = [row for row in rows if "signed_error_j" in row]
    return {
        "cutoffs": len(rows),
        "scored_cutoffs": len(available),
        "mean_abs_energy_error_kwh": statistics.mean(
            abs(row["signed_error_j"]) / 3_600_000 for row in available
        )
        if available
        else None,
        "mean_abs_arrival_charge_error_pp": statistics.mean(
            abs(row["arrival_soc_error"]) * 100
            for row in available
            if row["arrival_soc_error"] is not None
        )
        if any(row["arrival_soc_error"] is not None for row in available)
        else None,
        "false_safe": sum(row["false_safe"] for row in available),
        "reference_below_reserve": sum(row["reference_below_reserve"] for row in rows),
        "median_calculation_ms": 1000
        * statistics.median(row["elapsed_real_life_s"] for row in rows),
    }


def compare(folder: Path) -> dict:
    report = read(folder / "study-result.json")
    outer = read(folder / "qualification.json")
    if (
        report["partition"] not in ("development", "validation")
        or report["study_id"] != "expanded-routes-v1"
        or report["candidate"] != candidate()
        or not report["passed"]
        or not report["cleanup_verified"]
        or not outer["passed"]
        or outer["worker"] != {**report, "source_unchanged": True}
    ):
        raise ValueError("Use passing guarded preparation evidence for this exact expanded model")
    paired = {}
    for trip in report["trips"]:
        root = folder / trip["id"]
        reference = root / "reference.json"
        if hashlib.sha256(reference.read_bytes()).hexdigest() != trip["reference_sha256"]:
            raise ValueError("Retained SUMO reference differs from its recorded fingerprint")
        truth = read(reference)["rows"]
        sources = sorted(root.glob("input-*.json"))
        if len(sources) != len(trip["forecasts"]):
            raise ValueError("Retained inputs do not match the guarded cutoff count")
        for source in sources:
            index = int(source.stem.split("-")[1])
            request = decode_record(ForecastRequest, read(source))
            recorded = read(root / f"result-{index}.json")
            if request.state.wire_sha256 != fingerprint(truth[index]) or recorded[
                "input_sha256"
            ] != fingerprint(request):
                raise ValueError("Request is not bound to the retained cutoff observation")
            full = None
            for name, altered in alternatives(request):
                start = time.perf_counter()
                result = forecast(altered)
                elapsed = time.perf_counter() - start
                row = score(altered, result, truth, index, elapsed)
                row["reference_below_reserve"] = (
                    row["reference_minimum_soc"] < request.vehicle.reserve_soc - 1e-6
                )
                if name == "full":
                    full = row
                else:
                    paired.setdefault(name, []).append((full, row))
    return {
        "study": report["study_id"],
        "partition": report["partition"],
        "source_result_sha256": hashlib.sha256(
            (folder / "study-result.json").read_bytes()
        ).hexdigest(),
        "scope": "Matched preparation cutoffs with independent SUMO outcomes and local calculation timing",
        "factors": {
            factor: {
                "full": summarize([before for before, _ in rows]),
                "altered": summarize([after for _, after in rows]),
            }
            for factor, rows in paired.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    folder, output = args.result.resolve(), args.output.resolve()
    evidence = ROOT / "evidence/energy-v2"
    if not folder.is_relative_to(evidence) or output.parent != evidence or output.exists():
        parser.error("Use a retained result and a new output file under evidence/energy-v2")
    summary = compare(folder)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(summary["factors"], indent=2))


if __name__ == "__main__":
    main()
