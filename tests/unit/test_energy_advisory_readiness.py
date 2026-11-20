"""Readiness must include original work and never promote incomplete or changed evidence."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "phase2_review", ROOT / "scripts/known_route_energy/review_energy_advisory.py"
)
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def checklist(historical=True):
    name = "readiness-checklist-1.0.0.json" if historical else "readiness-checklist.json"
    return json.loads((ROOT / "qualification/energy" / name).read_bytes())


def test_historical_review_withholds_release_for_explicit_remaining_work():
    result = review.readiness(checklist(), True)
    assert not result["phase_2_ready"]
    assert result["unresolved_gaps"] == ["G-01", "G-02", "G-03"]
    assert set(result["incomplete_items"]) == {"P2-W03", "P2-W06", "P2-04"}
    assert not result["automatic_dispatch_approved"] and not result["runtime_model_flags_changed"]


@pytest.mark.parametrize("integrity", [False, None, "true", "false", 1])
def test_even_complete_checklist_requires_actual_boolean_integrity_pass(integrity):
    value = checklist(historical=False)
    assert not review.readiness(value, integrity, True)["phase_2_ready"]
    assert review.readiness(value, True, True)["phase_2_ready"]
    assert not review.readiness(value, True, True)["automatic_dispatch_approved"]


@pytest.mark.parametrize("evidence", [False, None, 1, "true"])
def test_closed_checklist_cannot_substitute_for_a_passed_current_integration(evidence):
    result = review.readiness(checklist(historical=False), True, evidence)
    assert result["unresolved_gaps"] == []
    assert result["phase_2_ready"] is False


def test_closed_checklist_must_retain_each_resolved_original_gap():
    value = checklist(historical=False)
    value["resolved_gaps"].pop()
    with pytest.raises(ValueError, match="all three"):
        review.readiness(value, True, True)


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "unknown", "deferred", "hidden_gap", "unreferenced_gap"]
)
def test_scope_and_gap_manipulation_is_refused(change):
    value = checklist()
    if change == "missing":
        value["items"].pop()
    elif change == "duplicate":
        value["items"].append(copy.deepcopy(value["items"][0]))
    elif change == "unknown":
        value["items"][0]["id"] = "REPLACEMENT"
    elif change == "deferred":
        value["items"][2]["status"] = "deferred_to_phase6"
    elif change == "hidden_gap":
        value["items"][2]["status"] = "passed"
    else:
        value["open_gaps"].append({"id": "unreferenced"})
    with pytest.raises(ValueError):
        review.readiness(value, True)


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("promotion_withheld_until_all_items_pass", False),
        ("automatic_dispatch_approved", True),
        ("phase3_authorized", True),
        ("phase6_deferrals_changed", True),
    ],
)
def test_review_cannot_enable_control_or_silently_defer_original_scope(field, new_value):
    value = checklist()
    value[field] = new_value
    with pytest.raises(ValueError):
        review.readiness(value, True)


def test_changed_or_redirected_evidence_is_not_accepted(tmp_path):
    file = tmp_path / "evidence.json"
    file.write_text('{"passed":true}')
    expected = {"evidence.json": review.sha(file)}
    assert review.check_files(tmp_path, expected) == 1
    file.write_text('{"passed":false}')
    with pytest.raises(ValueError, match="fingerprint"):
        review.check_files(tmp_path, expected)
    with pytest.raises(ValueError, match="escapes"):
        review.child(tmp_path, "../outside")
