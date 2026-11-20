import copy

import pytest

from evdt.monitoring.metrics import queue_diagnostics


def sender():
    return {
        "available": True,
        "dropped_records": 2,
        "physical_quota_enforced": True,
        "queue": {
            "generated": 10,
            "queued": 3,
            "pending": 4,
            "accepted": 3,
            "rejected": 1,
            "dropped": 2,
            "evicted_unconfirmed": 1,
            "evicted_lifetime": 3,
        },
        "storage": {"profile": "vehicle-queue-storage/1.0.0", "physical_quota_enforced": True},
    }


def test_confirmed_drops_and_unresolved_evictions_are_distinct():
    measured = sender()
    result = queue_diagnostics(measured)
    assert result["dropped_records"] == 2
    assert "1 evicted originals still await reconciliation" in result["drop_status"]
    assert result["physical_quota_enforced"] is True
    assert measured == sender()


@pytest.mark.parametrize(
    "change",
    [
        {"available": False},
        {"available": 1},
        {"dropped_records": None},
        {"dropped_records": True},
        {"dropped_records": 3},
        {"queue": {"pending": 0}},
    ],
)
def test_unavailable_legacy_or_inconsistent_counts_never_become_zero_loss(change):
    result = queue_diagnostics({**sender(), **change})
    assert result["dropped_records"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("generated", 11),
        ("pending", 5),
        ("queued", 2),
        ("dropped", -1),
        ("evicted_lifetime", 1),
        ("accepted", True),
    ],
)
def test_corrupt_accounting_is_not_reported_as_an_instrumented_count(field, value):
    measured = copy.deepcopy(sender())
    measured["queue"][field] = value
    assert queue_diagnostics(measured)["dropped_records"] is None


@pytest.mark.parametrize(
    "storage",
    [
        None,
        {},
        {"profile": "other", "physical_quota_enforced": True},
        {"profile": "vehicle-queue-storage/1.0.0", "physical_quota_enforced": 1},
    ],
)
def test_absent_or_unverified_provider_never_claims_a_physical_bound(storage):
    measured = {**sender(), "storage": storage}
    assert queue_diagnostics(measured)["physical_quota_enforced"] is False


def test_stale_sender_cannot_retain_a_current_physical_quota_claim():
    assert queue_diagnostics({**sender(), "available": False})["physical_quota_enforced"] is False
