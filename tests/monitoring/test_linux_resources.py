import copy
from pathlib import Path

import pytest

from scripts.monitoring import linux_resources as collector


@pytest.mark.parametrize("allow_replacement,failures", [(True, 1), (False, 1), (True, 2)])
def test_replacement_can_resample_one_complete_inventory(monkeypatch, allow_replacement, failures):
    inventories = iter([["old"], ["new"]])
    inspected = []

    class Engine:
        api_version = "1.56"

        def containers(self):
            return next(inventories)

        def inspect(self, ids):
            if not ids:
                return []
            inspected.append(ids)
            if len(inspected) <= failures:
                raise collector.ContainerDisappeared("Listed Docker container disappeared")
            return [
                {"service": "api", "State": {"Running": False, "OOMKilled": False, "ExitCode": 0}}
            ]

    monkeypatch.setattr(
        collector,
        "kernel_sample",
        lambda groups, stopped: {"containers": groups, "stopped": stopped},
    )
    if allow_replacement and failures == 1:
        row = collector.sample(Engine(), allow_replacement=True)
        assert row["replacement_resampled"] is True
        assert row["stopped"] == [{"service": "api", "oom_killed": False, "exit_code": 0}]
    else:
        with pytest.raises(collector.ContainerDisappeared):
            collector.sample(Engine(), allow_replacement=allow_replacement)
    assert inspected == ([["old"], ["new"]] if allow_replacement else [["old"]])


@pytest.mark.parametrize("still_running", [False, True])
def test_simultaneous_source_exit_races_are_rechecked_together_and_running_gaps_fail(
    monkeypatch, still_running
):
    ids = ["a" * 64, "b" * 64]
    calls = []

    class Engine:
        api_version = "1.56"

        def containers(self):
            return ids

        def inspect(self, selected):
            calls.append(selected)
            assert selected == ids
            first = len(calls) == 1
            return [
                {
                    "Id": identity,
                    "service": "source" + str(i),
                    "State": {
                        "Running": True if first else still_running,
                        "Pid": 1000 + i,
                        "OOMKilled": False,
                        "ExitCode": 0,
                    },
                }
                for i, identity in enumerate(ids)
            ]

    original = Path.read_text

    def read(path, *args, **kwargs):
        text = str(path)
        if text in ["/proc/1000/cgroup", "/proc/1001/cgroup"]:
            raise FileNotFoundError(text)
        if text == "/proc/vmstat":
            return "pswpin 0\npswpout 0\n"
        if text == "/proc/meminfo":
            return "MemTotal: 1000 kB\nMemAvailable: 900 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\nCached: 10 kB\nSReclaimable: 0 kB\n"
        if text == "/proc/sys/kernel/random/boot_id":
            return "test-boot\n"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    if still_running:
        with pytest.raises(ValueError, match="Running container cgroup"):
            collector.sample(Engine())
    else:
        row = collector.sample(Engine())
        assert not row["containers"] and len(row["stopped"]) == 2
        assert all(r["exit_code"] == 0 and not r["oom_killed"] for r in row["stopped"])
        assert row["collection_span_s"] >= row["race_reinspect_s"] >= 0
    assert len(calls) == 2


@pytest.mark.parametrize(
    "service,settled,oom,resamples,inspection_count",
    [
        ("energy-worker", True, False, True, 3),
        ("energy-worker", True, True, True, 3),
        ("energy-worker", False, False, True, 4),
        ("api", True, False, False, 2),
    ],
)
def test_finite_job_cgroup_exit_race_resamples_once(
    monkeypatch, service, settled, oom, resamples, inspection_count
):
    inspections = []
    waits = []

    class Engine:
        api_version = "1.56"

        def containers(self):
            return ["job"]

        def inspect(self, ids):
            if not ids:
                return []
            inspections.append(ids)
            finished = settled and len(inspections) > 2
            return [
                {
                    "Id": "job",
                    "service": service,
                    "State": {
                        "Running": not finished,
                        "OOMKilled": oom,
                        "ExitCode": 137 if oom else 0,
                    },
                }
            ]

    def missing(_container):
        raise FileNotFoundError("/proc/job/cgroup")

    monkeypatch.setattr(collector, "group_location", missing)
    monkeypatch.setattr(collector.time, "sleep", waits.append)
    monkeypatch.setattr(
        collector,
        "kernel_sample",
        lambda groups, stopped: {"containers": groups, "stopped": stopped},
    )
    if settled and resamples:
        result = collector.sample(
            Engine(), allow_replacement=True, finite_services=("energy-worker",)
        )
        assert result["replacement_resampled"] is True
        assert result["stopped"] == [
            {"service": service, "oom_killed": oom, "exit_code": 137 if oom else 0}
        ]
    else:
        with pytest.raises(ValueError, match="Running container cgroup"):
            collector.sample(Engine(), allow_replacement=True, finite_services=("energy-worker",))
    assert waits == ([0.05] if resamples else [])
    assert len(inspections) == inspection_count


def test_frozen_counters_are_fresh_without_repeated_daemon_calls(monkeypatch):
    row = {
        "Id": "a" * 64,
        "service": "source",
        "RestartCount": 0,
        "State": {
            "Running": True,
            "Pid": 1000,
            "OOMKilled": False,
            "ExitCode": 0,
            "StartedAt": "start",
        },
    }

    class Engine:
        project = "evdt-e2c-" + "a" * 12
        api_version = "1.56"
        calls = 0

        def containers(self):
            self.calls += 1
            return [row["Id"]]

        def inspect(self, ids):
            assert ids == [row["Id"]]
            return [copy.deepcopy(row)]

    engine = Engine()
    monkeypatch.setattr(collector, "group_location", lambda r: ("/owned", Path("/owned")))
    counter = {"value": 1, "absent": False}

    def read(service, relative, path):
        if counter["absent"]:
            raise FileNotFoundError(path)
        return {"service": service, "group": relative, "memory_bytes": counter["value"]}

    monkeypatch.setattr(collector, "group_sample", read)
    monkeypatch.setattr(
        collector,
        "kernel_sample",
        lambda groups, stopped: {"containers": groups, "stopped": stopped},
    )
    frozen = collector.FrozenGroups(engine, ["source"])
    assert frozen.sample()["containers"][0]["memory_bytes"] == 1
    counter["value"] = 200
    assert frozen.sample()["containers"][0]["memory_bytes"] == 200
    counter["absent"] = True
    missing = frozen.sample()
    assert missing["containers"] == [] and missing["stopped"] == []
    assert missing["absent"] == [{"service": "source", "group": "/owned"}]
    assert engine.calls == 1
    with pytest.raises(ValueError, match="graph"):
        collector.FrozenGroups(engine, ["api"])


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "id",
        "restart",
        "start",
        "pid",
        "oom",
        "service",
        "source_running",
        "source_failure",
        "api_stopped",
    ],
)
def test_frozen_final_inventory_refuses_changes_and_abnormal_exits(fault):
    baseline = [
        {
            "Id": str(i) * 64,
            "service": name,
            "RestartCount": 0,
            "State": {
                "Running": True,
                "Pid": 1000 + i,
                "OOMKilled": False,
                "ExitCode": 0,
                "StartedAt": "start",
            },
        }
        for i, name in enumerate(["api", "source"], 1)
    ]
    final = copy.deepcopy(baseline)
    final[1]["State"].update(Running=False, Pid=0)
    if fault == "id":
        final[1]["Id"] = "c" * 64
    elif fault == "restart":
        final[0]["RestartCount"] = 1
    elif fault == "start":
        final[0]["State"]["StartedAt"] = "later"
    elif fault == "pid":
        final[0]["State"]["Pid"] = 42
    elif fault == "oom":
        final[1]["State"]["OOMKilled"] = True
    elif fault == "service":
        final[0]["service"] = "other"
    elif fault == "source_running":
        final[1]["State"]["Running"] = True
    elif fault == "source_failure":
        final[1]["State"]["ExitCode"] = 1
    elif fault == "api_stopped":
        final[0]["State"].update(Running=False, Pid=0)
    if fault:
        with pytest.raises(ValueError):
            collector.validate_final(baseline, final)
    else:
        collector.validate_final(baseline, final)
