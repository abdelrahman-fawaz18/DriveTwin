"""Exercise the real receiver validator and the frozen historical reader."""

import argparse
import base64
import importlib.util
import json
from pathlib import Path

from evdt.contracts import Contract, Rejected

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--typescript-results", type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location(
        "frozen_legacy", ROOT / "qualification/contracts/legacy_contract_1_0.py"
    )
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    legacy_catalog = json.loads((ROOT / "catalog/reference-signals.json").read_text())
    legacy_validator = legacy.Contract(legacy_catalog)
    results = []
    ts_results = {
        result["id"]: result for result in json.loads(args.typescript_results.read_text())
    }
    for case in json.loads(args.cases.read_text()):
        result = {"id": case["id"], "accepted": False, "reason": None}
        try:
            catalog = case.get("catalog")
            validator = Contract(catalog)
        except ValueError:
            result["reason"] = "configuration_error"
            results.append(result)
            continue
        if case.get("mutate_catalog_after"):
            catalog["signals"]["battery.pack.soc"]["unit"] = "changed-after-construction"
        raw = base64.b64decode(case["raw_base64"])
        try:
            validator.validate(raw, case["topic"], case["expected_run"])
            result["accepted"] = True
        except Rejected as exc:
            result["reason"] = str(exc)
        if "legacy_expected" in case:
            try:
                legacy_validator.validate(raw, case["topic"], case["expected_run"])
                result["legacy_accepted"] = True
            except legacy.Rejected:
                result["legacy_accepted"] = False
        if case.get("downgrade"):
            encoded = validator.for_legacy(raw, case["topic"], case["expected_run"])
            legacy_validator.validate(encoded, case["topic"], case["expected_run"])
            result["downgrade"] = json.loads(encoded)
            ts_encoded = base64.b64decode(ts_results[case["id"]]["downgrade_raw_base64"])
            legacy_validator.validate(ts_encoded, case["topic"], case["expected_run"])
            result["legacy_typescript_adapter_accepted"] = True
        results.append(result)
    args.output.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
