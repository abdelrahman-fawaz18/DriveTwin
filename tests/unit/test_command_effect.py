"""Uninformative or contradictory measurements cannot confirm a speed command."""

import pytest

from evdt.command_effect import compare_speed

BASELINE = [2.0] * 5 + [8.0] * 11
CAPPED = [2.0] * 5 + [4.0] * 11


@pytest.mark.parametrize(
    "baseline,commanded,status",
    [
        (BASELINE, CAPPED, "confirmed"),
        (BASELINE, BASELINE, "not_observed"),
        (CAPPED, CAPPED, "inconclusive"),
        (BASELINE, [1.0, *CAPPED[1:]], "inconclusive"),
        (BASELINE, [*CAPPED[:-1], 4.5], "inconclusive"),
    ],
)
def test_speed_evidence_keeps_missing_effect_distinct_from_positive_confirmation(
    baseline, commanded, status
):
    result = compare_speed(baseline, commanded, 4.0)
    assert result["status"] == status
    assert result["confirmed"] is (status == "confirmed")
