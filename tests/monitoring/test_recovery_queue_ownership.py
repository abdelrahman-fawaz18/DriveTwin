"""Recovery never adopts foreign queue storage or hides an unlabeled volume."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def recovery():
    path = Path(__file__).resolve().parents[2] / "scripts/monitoring/recovery_fixture.py"
    spec = importlib.util.spec_from_file_location("recovery_queue_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def owned_volume():
    return {
        "Name": "fixture-restore_spool2",
        "Labels": {
            "com.docker.compose.project": "fixture-restore",
            "com.docker.compose.volume": "spool2",
        },
    }


def test_queue_is_compose_owned_without_starting_a_writer(recovery):
    compose = Mock()
    execute = Mock(side_effect=["", json.dumps([owned_volume()])])
    result = recovery.prepare_restore_queue(compose, execute, "fixture-restore")
    assert result == {"passed": True, "compose_owned": True, "service_started": False}
    assert compose.call_count == 1
    assert compose.call_args.args[0] == "create"
    assert not any(call.args[0] in ("up", "start", "run") for call in compose.call_args_list)


def test_existing_queue_is_refused_before_any_compose_mutation(recovery):
    compose = Mock()
    execute = Mock(return_value="fixture-restore_spool2\n")
    with pytest.raises(RuntimeError, match="already exists"):
        recovery.prepare_restore_queue(compose, execute, "fixture-restore")
    compose.assert_not_called()


@pytest.mark.parametrize(
    "labels",
    [
        None,
        {},
        {"com.docker.compose.project": "other", "com.docker.compose.volume": "spool2"},
        {"com.docker.compose.project": "fixture-restore", "com.docker.compose.volume": "other"},
    ],
)
def test_missing_or_foreign_volume_labels_cannot_qualify(recovery, labels):
    volume = {**owned_volume(), "Labels": labels}
    execute = Mock(side_effect=["", json.dumps([volume])])
    with pytest.raises(RuntimeError, match="must be owned"):
        recovery.prepare_restore_queue(Mock(), execute, "fixture-restore")


def test_wrong_volume_identity_cannot_qualify(recovery):
    volume = {**owned_volume(), "Name": "other_spool2"}
    execute = Mock(side_effect=["", json.dumps([volume])])
    with pytest.raises(RuntimeError, match="Wrong restore queue"):
        recovery.prepare_restore_queue(Mock(), execute, "fixture-restore")
