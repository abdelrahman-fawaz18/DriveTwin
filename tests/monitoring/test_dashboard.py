"""Read boundary tests; actual transaction and browser behavior use real services."""

import pytest
from fastapi import HTTPException

from evdt.monitoring.dashboard import MAX_REVISION, cursor, parse_cursor


def test_scoped_cursor_and_safe_integer():
    for revision in (0, 1, MAX_REVISION):
        assert parse_cursor(cursor("run", "bus-001", revision), "run", "bus-001") == revision
    for bad in (
        "run:bus-002:1",
        "other:bus-001:1",
        "run:bus-001:-1",
        "run:bus-001:01",
        f"run:bus-001:{MAX_REVISION + 1}",
        "run:bus-001:nan",
        "broken",
        None,
    ):
        with pytest.raises(HTTPException) as error:
            parse_cursor(bad, "run", "bus-001")
        assert error.value.status_code == 409
