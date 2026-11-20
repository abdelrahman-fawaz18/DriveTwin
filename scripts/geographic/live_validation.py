"""Reconcile a finite live source against committed observations and history."""

import math


def initial_charge_receipt(source_report, recipe, profile):
    """Verify the insertion measurement emitted by the actual SUMO adapter."""
    summary = source_report["plant_summary"]
    expected_j = recipe["capacity_kwh"] * 3_600_000 * recipe["initial_soc"]
    observed_j = summary["insertion"]["energy_j"]
    passed = math.isfinite(observed_j) and abs(observed_j - expected_j) <= 0.01
    return {
        "passed": passed,
        "requested_percent": profile.get("starting_charge_percent"),
        "configured_percent": round(recipe["initial_soc"] * 100, 9),
        "configured_energy_j": expected_j,
        "measured_insertion_energy_j": observed_j,
        "observed_working_s": summary["insertion"]["sim_time_s"],
        "tolerance_j": 0.01,
    }


def startup_forecast(view):
    """Accept a fresh physical infeasibility result as a working forecast service."""
    if view["current"] is not None:
        return view["current"]
    diagnostic = view.get("diagnostic")
    if diagnostic and diagnostic["payload"]["status"] == "infeasible":
        return diagnostic["payload"]
    return None


def validate_current_forecast(view, policy):
    if view["current"] is None:
        return
    working_age = view["working_age_s"]
    real_life_age = view["real_life_age_s"]
    if not (
        view["status"] == "available"
        and 0 <= working_age <= policy.forecast_max_age_working_s
        and -0.05 <= real_life_age <= policy.forecast_max_age_real_life_s
    ):
        raise RuntimeError("Current forecast violated the assigned freshness limits")


def reconcile(events, committed, sample_counts):
    expected = {event["event_id"]: event["wire_sha256"] for event in events}
    actual = {str(identity): digest for identity, digest in committed}
    if not events or len(expected) != len(events) or expected != actual:
        raise ValueError("Committed observation identities or wire fingerprints differ")
    if len(committed) != len(events):
        raise ValueError("Duplicate committed observations")
    signals = {sample["signal_id"] for sample in events[0]["body"]["samples"]}
    if len(signals) != 20 or any(
        len(event["body"]["samples"]) != 20
        or {sample["signal_id"] for sample in event["body"]["samples"]} != signals
        for event in events
    ):
        raise ValueError("The geographic source must record all twenty signals at every tick")
    times = [event["body"]["sim_time_s"] for event in events]
    if any(not math.isclose(b - a, 1, abs_tol=1e-6) for a, b in zip(times, times[1:])):
        raise ValueError("The one-working-second observation cadence has a gap")
    if dict(sample_counts) != {signal: len(events) for signal in signals}:
        raise ValueError("Recorded signal history differs from the source")
    return {
        "passed": True,
        "events": len(events),
        "signals": 20,
        "readings": len(events) * 20,
        "wire_fingerprints_match": True,
        "working_cadence_s": 1,
    }
