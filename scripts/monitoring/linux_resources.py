"""Sample only this fixture's disjoint cgroups and whole-Linux paging counters."""

import argparse
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

if __package__:
    from .docker_resources import ContainerDisappeared, DockerReadClient
else:
    from docker_resources import ContainerDisappeared, DockerReadClient

ROOT = Path(__file__).resolve().parents[2]
FROZEN_POLICY = "frozen-cgroup/1.0.0"


class RunningGroupUnavailable(ValueError):
    def __init__(self, service):
        super().__init__("Running container cgroup could not be read")
        self.service = service


def numbers(path):
    return {
        key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())
    }


def group_location(container):
    pid = container["State"]["Pid"]
    hierarchy = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
    relative = next(line[3:] for line in hierarchy if line.startswith("0::"))
    return relative, Path("/sys/fs/cgroup") / relative.lstrip("/")


def group_sample(service, relative, path):
    return {
        "service": service,
        "group": relative,
        "memory_bytes": int((path / "memory.current").read_text()),
        "memory_stat": numbers(path / "memory.stat"),
        "memory_events": numbers(path / "memory.events"),
        "memory_limit": (path / "memory.max").read_text().strip(),
        "cpu": numbers(path / "cpu.stat"),
    }


class FrozenGroups:
    """Cache identities/locations only; read fresh kernel counters on every tick."""

    def __init__(self, engine, expected):
        self.engine = engine
        self.baseline = engine.inspect(engine.containers())
        active = [r for r in self.baseline if r["service"] not in ("probe", "migrate")]
        if len(active) != len(expected) or {r["service"] for r in active} != set(expected):
            raise ValueError("Frozen resource service graph differs")
        if any(not r["State"]["Running"] or r["State"]["OOMKilled"] for r in active):
            raise ValueError("Frozen resource service is not healthy and running")
        self.locations = [(r["service"], *group_location(r)) for r in active]

    def sample(self):
        started = time.monotonic()
        groups, absent = [], []
        for service, relative, path in self.locations:
            try:
                groups.append(group_sample(service, relative, path))
            except FileNotFoundError:
                # Absence is not an invented zero/normal exit. Final inspection
                # and active-window coverage must independently explain it.
                absent.append({"service": service, "group": relative})
        return {
            **kernel_sample(groups, []),
            "absent": absent,
            "collection_span_s": time.monotonic() - started,
            "docker_api_version": self.engine.api_version,
            "collector_policy": FROZEN_POLICY,
        }

    def finish(self):
        final = self.engine.inspect(self.engine.containers())
        validate_final(self.baseline, final)
        return {
            "passed": True,
            "policy": FROZEN_POLICY,
            "project": self.engine.project,
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "baseline": self.baseline,
            "final": final,
        }


def validate_final(baseline, final):
    before = {r["Id"]: r for r in baseline}
    after = {r["Id"]: r for r in final}
    if len(before) != len(baseline) or len(after) != len(final) or before.keys() != after.keys():
        raise ValueError("Frozen resource container identities changed")
    for identity, row in after.items():
        old, state = before[identity], row["State"]
        if (
            row["service"] != old["service"]
            or row["RestartCount"] != old["RestartCount"]
            or state["StartedAt"] != old["State"]["StartedAt"]
            or state["OOMKilled"]
            or (state["Running"] and state["Pid"] != old["State"]["Pid"])
        ):
            raise ValueError("Frozen resource service restarted or changed")
        source = re.fullmatch(r"source(?:[2-9]|10)?", row["service"])
        if source or row["service"] == "migrate":
            if state["Running"] or state["ExitCode"] != 0:
                raise ValueError("Frozen finite service did not finish normally")
        elif not state["Running"]:
            raise ValueError("Frozen persistent service stopped")


def sample(engine, *, allow_replacement=False, restartable_services=(), finite_services=()):
    started = time.monotonic()
    try:
        return sample_inventory(engine, restartable_services)
    except (ContainerDisappeared, RunningGroupUnavailable) as exc:
        if not allow_replacement:
            raise
        if isinstance(exc, RunningGroupUnavailable):
            if exc.service not in finite_services:
                raise
            # The kernel can remove a finite job's counters before Docker records its exit.
            time.sleep(0.05)
        # Take a fresh complete sample when a test deliberately replaces services.
        row = sample_inventory(engine, restartable_services)
        row["collection_span_s"] = time.monotonic() - started
        row["replacement_resampled"] = True
        return row


def sample_inventory(engine, restartable_services):
    started = time.monotonic()
    ids = engine.containers()
    listed = time.monotonic()
    containers = engine.inspect(ids)
    inspected = time.monotonic()
    groups = []
    stopped = []
    raced = []
    for container in containers:
        state = container["State"]
        service = container["service"]
        if service in ("probe", "migrate"):
            continue
        if not state["Running"]:
            stopped.append(
                {
                    "service": service,
                    "oom_killed": state["OOMKilled"],
                    "exit_code": state["ExitCode"],
                }
            )
            continue
        try:
            relative, path = group_location(container)
            group = group_sample(service, relative, path)
            if service in restartable_services:
                group["container_started_at"] = state["StartedAt"]
            groups.append(group)
        except FileNotFoundError:
            raced.append(container["Id"])
    read_at = time.monotonic()
    # Recheck simultaneous source exits together instead of spawning one CLI per exit.
    for container in engine.inspect(raced):
        again = container["State"]
        if again["Running"]:
            raise RunningGroupUnavailable(container["service"])
        stopped.append(
            {
                "service": container["service"],
                "oom_killed": again["OOMKilled"],
                "exit_code": again["ExitCode"],
            }
        )
    raced_at = time.monotonic()
    return {
        **kernel_sample(groups, stopped),
        "collection_span_s": time.monotonic() - started,
        "docker_list_s": listed - started,
        "docker_inspect_s": inspected - listed,
        "cgroup_read_s": read_at - inspected,
        "race_reinspect_s": raced_at - read_at,
        "docker_api_version": engine.api_version,
        "collector_policy": "docker-unix-read/1.0.0",
    }


def kernel_sample(groups, stopped):
    paths = [g["group"].rstrip("/") for g in groups]
    if len(paths) != len(set(paths)) or any(
        a != b and b.startswith(a + "/") for a in paths for b in paths
    ):
        raise ValueError("Container accounting boundaries overlap")
    vm = numbers(Path("/proc/vmstat"))
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, rest = line.split(":")
        if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "Cached", "SReclaimable"):
            memory[key] = int(rest.split()[0]) * 1024
    return {
        "utc_s": time.time(),
        "monotonic_s": time.monotonic(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "page_bytes": os.sysconf("SC_PAGE_SIZE"),
        "swap_in_pages": vm["pswpin"],
        "swap_out_pages": vm["pswpout"],
        "linux_memory": memory,
        "containers": groups,
        "stopped": stopped,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stop", type=Path, required=True)
    parser.add_argument("--frozen-services", nargs="+")
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--phase6-monitoring", "--phase6-smoke", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"evdt-e2[bc]-[a-f0-9]{12}", args.project):
        parser.error("Require an exact owned E2-B/C project")
    if not args.output.resolve().is_relative_to(
        ROOT / ("evidence/p6" if args.phase6_monitoring else "evidence/p1")
    ) or not args.stop.resolve().is_relative_to(ROOT / ".runtime"):
        parser.error("Require private project evidence/control paths")
    if bool(args.frozen_services) != bool(args.ready) or (
        args.ready and not args.ready.resolve().is_relative_to(ROOT / ".runtime")
    ):
        parser.error("Frozen collection requires its private readiness path")
    guard = None
    if args.phase6_monitoring:
        if not args.frozen_services:
            parser.error("Phase 6 requires frozen resource identities")
        sys.path.insert(0, str(ROOT / "scripts/release_checks"))
        from smoke_policy import LinuxSmokeGuard

        guard = LinuxSmokeGuard()
    interrupted = False

    def stop(_signum, _frame):
        nonlocal interrupted
        interrupted = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    output = args.output.open("x", encoding="utf-8")
    engine = None
    try:
        engine = DockerReadClient(args.project)
        frozen = FrozenGroups(engine, args.frozen_services) if args.frozen_services else None
        started = time.monotonic()
        sequence = 0
        previous_write_s = None
        while not args.stop.exists() and not interrupted:
            record = {"sequence": sequence, **(frozen.sample() if frozen else sample(engine))}
            record["previous_write_s"] = previous_write_s
            writing = time.monotonic()
            output.write(json.dumps(record, allow_nan=False) + "\n")
            output.flush()
            if guard:
                guard.observe(record)
            previous_write_s = time.monotonic() - writing
            sequence += 1
            if frozen and sequence == 1:
                args.ready.write_text(
                    json.dumps({"policy": FROZEN_POLICY, "project": args.project})
                )
            time.sleep(max(0, started + sequence - time.monotonic()))
        if frozen and not interrupted:
            args.output.with_suffix(".final.json").write_text(json.dumps(frozen.finish()) + "\n")
    except Exception as exc:
        if guard:
            args.output.with_name("linux-guard.json").write_text(
                json.dumps({"reason": str(exc)}) + "\n", encoding="utf-8"
            )
        raise
    finally:
        if engine is not None:
            engine.close()
        output.close()
    if interrupted:
        raise SystemExit("Resource collector interrupted; preserved partial samples")


if __name__ == "__main__":
    main()
