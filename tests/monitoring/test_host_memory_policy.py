import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def load(monkeypatch):
    folder = Path(__file__).resolve().parents[2] / "scripts/monitoring"
    monkeypatch.syspath_prepend(str(folder))
    spec = importlib.util.spec_from_file_location(
        "host_memory_policy", folder / "host_memory_policy.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows():
    return [
        {
            "monotonic_s": i,
            "physical_available_bytes": 1024**3,
            "commit": {
                "total_bytes": 4 * 1024**3,
                "limit_bytes": 8 * 1024**3,
                "available_bytes": 4 * 1024**3,
            },
        }
        for i in range(65)
    ]


def test_local_warning_never_claims_full_host_capacity(monkeypatch):
    result = load(monkeypatch).assess(rows())
    assert result["passed"] and result["capacity_warning"]
    assert result["host_capacity_qualified"] is False
    assert result["physical_emergency_upper_s"] == 0
    assert result["commit_headroom_min_bytes"] == 4 * 1024**3


@pytest.mark.parametrize("resource", ["physical", "commit"])
def test_sustained_critical_memory_aborts_and_transient_does_not(monkeypatch, resource):
    module = load(monkeypatch)
    for stop, expected in ((18, True), (23, False)):
        current = rows()
        for row in current[10:stop]:
            if resource == "physical":
                row["physical_available_bytes"] = 256 * 1024**2
            else:
                row["commit"]["available_bytes"] = 512 * 1024**2
                row["commit"]["total_bytes"] = row["commit"]["limit_bytes"] - 512 * 1024**2
        result = module.assess(current)
        assert result["passed"] is expected
        if not expected:
            assert resource in result["failure"]["reason"]


@pytest.mark.parametrize("fault", ["missing", "negative", "inconsistent", "boolean", "clock_gap"])
def test_missing_invalid_or_uncovered_counters_cannot_pass(monkeypatch, fault):
    module = load(monkeypatch)
    current = rows()
    if fault == "missing":
        del current[3]["commit"]
    elif fault == "negative":
        current[3]["physical_available_bytes"] = -1
    elif fault == "inconsistent":
        current[3]["commit"]["total_bytes"] += 1
    elif fault == "boolean":
        current[3]["physical_available_bytes"] = True
    else:
        current[3]["monotonic_s"] += 2
    guard = module.Guard()
    for row in current:
        guard.observe(row)
    assert guard.failure
    if fault == "clock_gap":
        assert module.assess(current)["passed"] is False
    else:
        with pytest.raises((ValueError, KeyError)):
            module.assess(current)


def test_policy_tampering_is_rejected_before_execution(monkeypatch, tmp_path):
    module = load(monkeypatch)
    configured = copy.deepcopy(module.POLICY)
    configured["emergency_available_bytes"] = 1
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "benchmarks/monitoring-host.json").write_text(json.dumps(configured))
    monkeypatch.setattr(module, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="Unreviewed"):
        module.Guard()


def test_emergency_stops_the_owned_child_and_no_other_process(monkeypatch, tmp_path):
    module = load(monkeypatch)
    original = subprocess.Popen
    children = []

    def capture(*args, **kwargs):
        process = original(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(module.subprocess, "Popen", capture)
    with pytest.raises(module.Emergency, match="controlled emergency"):
        module.run_guarded(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            env=None,
            sampler=SimpleNamespace(error=None),
            guard=SimpleNamespace(failure={"reason": "controlled emergency"}),
            timeout=5,
        )
    assert len(children) == 1 and children[0].poll() is not None


def test_healthy_owned_child_returns_output_normally(monkeypatch, tmp_path):
    module = load(monkeypatch)
    result = module.run_guarded(
        [sys.executable, "-c", "print('completed')"],
        cwd=tmp_path,
        env=None,
        sampler=SimpleNamespace(error=None),
        guard=SimpleNamespace(failure=None),
        timeout=5,
    )
    assert result.returncode == 0 and result.stdout.strip() == "completed"


def test_windows_emergency_force_fallback_targets_only_fresh_owned_tree(monkeypatch, tmp_path):
    module = load(monkeypatch)
    commands = []

    class Child:
        pid = 4321
        returncode = None

        def poll(self):
            return self.returncode

        def send_signal(self, value):
            raise OSError("Controlled unavailable console")

        def communicate(self, timeout):
            assert self.returncode is not None
            return "", ""

    child = Child()

    def stop(argv, **kwargs):
        commands.append(argv)
        child.returncode = -1

    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(module.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(module.subprocess, "run", stop)
    with pytest.raises(module.Emergency):
        module.run_guarded(
            ["owned-test-runner"],
            cwd=tmp_path,
            env=None,
            sampler=SimpleNamespace(error=None),
            guard=SimpleNamespace(failure={"reason": "controlled"}),
            timeout=5,
        )
    assert commands == [["taskkill", "/PID", "4321", "/T", "/F"]]


def test_partial_destination_is_linux_relative_and_cannot_escape_evidence(monkeypatch, tmp_path):
    module = load(monkeypatch)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    destination = tmp_path / "evidence/p1/run/services/partial/traces"
    assert module.evidence_destination(destination) == "evidence/p1/run/services/partial/traces"
    for invalid in (tmp_path / "src/file", tmp_path.parent / "outside"):
        with pytest.raises(ValueError):
            module.evidence_destination(invalid)


def test_setup_preparation_is_explicit_scoped_and_recorded(monkeypatch, tmp_path):
    module = load(monkeypatch)
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments/dev.toml").write_text('[linux]\ndistribution="test-distribution"\n')
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.os, "name", "nt")
    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        assert kwargs["check"] and kwargs["timeout"] == 30
        return SimpleNamespace(stdout='{"before_cached_bytes":100,"after_cached_bytes":50}')

    monkeypatch.setattr(module.subprocess, "run", run)
    result = module.reclaim_setup_cache()
    assert commands[0][:7] == [
        "wsl.exe",
        "--distribution",
        "test-distribution",
        "--user",
        "root",
        "--exec",
        "python3",
    ]
    assert "os.sync()" in commands[0][-1] and "/proc/sys/vm/drop_caches" in commands[0][-1]
    assert result == {
        "policy": "clean-file-cache-before-browser/1.0.0",
        "before_cached_bytes": 100,
        "after_cached_bytes": 50,
    }
