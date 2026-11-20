"""Assess a bounded pair of local simulator speed traces independently of acknowledgment."""

from evdt.contract_profile import validator

EVIDENCE = validator("commands/speed-evidence-1.0.0.schema.json")
APPLY_AT_S = 4
STOP_AT_S = 16


def compare_speed(baseline: list, commanded: list, limit: float) -> dict:
    """Inputs are validated speed readings from simulated seconds 1 through 16."""
    before_matches = baseline[:APPLY_AT_S] == commanded[:APPLY_AT_S]
    after = list(zip(baseline[APPLY_AT_S + 1 :], commanded[APPLY_AT_S + 1 :], strict=True))
    baseline_above_limit = sum(a > limit + 0.25 for a, _ in after)
    lower = sum(a > limit + 0.25 and b <= limit + 0.05 for a, b in after)
    maximum = max(b for _, b in after)
    status = "inconclusive"
    if before_matches and baseline_above_limit >= 2:
        if maximum <= limit + 0.05 and lower >= 2:
            status = "confirmed"
        elif all(a == b for a, b in after):
            status = "not_observed"
    return {
        "status": status,
        "confirmed": status == "confirmed",
        "signal_id": "motion.speed",
        "unit": "m/s",
        "matching_pre_command_samples": APPLY_AT_S if before_matches else 0,
        "post_command_pairs": len(after),
        "pairs_showing_lower_speed": lower,
        "maximum_baseline_m_s": max(a for a, _ in after),
        "maximum_commanded_m_s": maximum,
    }
