"""A transient cross-platform control-file handoff cannot masquerade as an outage fault."""

from pathlib import Path

import pytest

from scripts.monitoring.outage_fixture import read_state


def test_missing_locked_partial_handoffs_recover_within_budget(monkeypatch, tmp_path):
    values = iter([FileNotFoundError(), PermissionError(), b'{"generated":', b'{"generated": 12}'])
    reads = []

    def read(path):
        reads.append(path)
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(Path, "read_bytes", read)
    assert read_state(tmp_path / "latest.json", attempts=4, delay_s=0) == {"generated": 12}
    assert len(reads) == 4


def test_persistent_missing_file_is_not_hidden(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_state(tmp_path / "absent.json", attempts=3, delay_s=0)


def test_persistent_partial_json_is_not_hidden(tmp_path):
    path = tmp_path / "partial.json"
    path.write_bytes(b'{"generated":')
    with pytest.raises(ValueError):
        read_state(path, attempts=3, delay_s=0)
