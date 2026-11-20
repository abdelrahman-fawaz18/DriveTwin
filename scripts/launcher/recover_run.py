"""Recover the resources of one interrupted launcher trip."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")
RECEIPT = "cleanup-recovery.json"
BACKUP = "recovered-database.dump"


def project_name(folder):
    return "evdt-e2c-" + hashlib.sha256(folder.name.encode()).hexdigest()[:12]


def save_receipt(folder, data):
    temporary = folder / "cleanup-recovery.tmp"
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(folder / RECEIPT)


def read_receipt(folder):
    path = folder / RECEIPT
    try:
        if path.resolve() != path or path.stat().st_size > 65536:
            return {}
        receipt = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return {}
    if (
        isinstance(receipt, dict)
        and receipt.get("version") == 1
        and receipt.get("trip") == folder.name
        and receipt.get("project") == project_name(folder)
    ):
        return receipt
    return {}


def recovered_session(folder):
    receipt = read_receipt(folder)
    if receipt.get("cleanup_verified") is True:
        return {
            "cleanup_verified": True,
            "backup_saved": receipt.get("backup_saved") is True,
        }
    return {}


def valid_result(value):
    """A damaged local result cannot prove cleanup or a saved recording."""
    if not isinstance(value, dict) or not isinstance(value.get("status"), str):
        return False
    session = value.get("session", {})
    if not isinstance(session, dict) or any(
        name in session and type(session[name]) is not bool
        for name in ("cleanup_verified", "backup_saved")
    ):
        return False
    guard = value.get("runtime_host_guard")
    if guard is not None:
        if not isinstance(guard, dict):
            return False
        failure = guard.get("failure")
        if failure is not None and (
            not isinstance(failure, dict) or not isinstance(failure.get("reason"), str)
        ):
            return False
    return True


def archive_sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def docker(*args, timeout=35, stdout=subprocess.PIPE, stdin=None):
    return subprocess.run(
        ["docker", *args],
        stdout=stdout,
        stdin=stdin,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=True,
    ).stdout


def resources(project, kind):
    args = [kind, "ls", "-q", "--filter", "label=com.docker.compose.project=" + project]
    if kind == "container":
        args.extend(("--all", "--no-trunc"))
    return docker(*args).decode().split()


def dump_database(container, folder):
    backup = folder / BACKUP
    existing = backup.is_file()
    if backup.is_symlink() or (backup.exists() and not existing):
        raise ValueError("An existing recovered database archive needs inspection.")
    if existing:
        with backup.open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise ValueError("An existing recovered database archive needs inspection.")
    temporary = folder / "recovered-database.partial"
    docker("start", container)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                docker("exec", container, "pg_isready", "-U", "evdt_admin", "-d", "evdt", timeout=5)
                break
            except subprocess.CalledProcessError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)
        if existing:
            # A prior saved flag or magic header is not archive validation.
            with backup.open("rb") as stream:
                docker("exec", "-i", container, "pg_restore", "--list", stdin=stream, timeout=30)
            return True
        if temporary.exists():
            temporary = folder / f"recovered-database-{time.time_ns()}.partial"
        with temporary.open("xb") as output:
            docker(
                "exec",
                container,
                "pg_dump",
                "-U",
                "evdt_admin",
                "-d",
                "evdt",
                "-Fc",
                stdout=output,
                timeout=90,
            )
        with temporary.open("rb") as stream:
            docker("exec", "-i", container, "pg_restore", "--list", stdin=stream, timeout=30)
        temporary.rename(backup)
        return True
    finally:
        docker("stop", "--time", "10", container)


def release_trip(folder):
    previous = read_receipt(folder)
    if previous.get("cleanup_verified") is True:
        return
    original = folder / "operator-result.json"
    if original.is_file():
        try:
            result = json.loads(original.read_bytes())
            if valid_result(result) and result.get("session", {}).get("cleanup_verified") is True:
                return
        except (AttributeError, ValueError):
            pass
    project = project_name(folder)
    ids = resources(project, "container")
    retained = set(resources(project, "volume"))
    retained.update(previous.get("retained_volumes", []))
    db = None
    running = []
    for container in ids:
        label, service, state, mounts = json.loads(
            docker(
                "container",
                "inspect",
                "--format",
                '[{{json (index .Config.Labels "com.docker.compose.project")}},'
                '{{json (index .Config.Labels "com.docker.compose.service")}},'
                "{{json .State.Status}},{{json .Mounts}}]",
                container,
            )
        )
        if label != project:
            raise ValueError("A container does not belong to the interrupted trip.")
        retained.update(mount["Name"] for mount in mounts if mount["Type"] == "volume")
        if state in ("running", "restarting"):
            running.append(container)
        elif state not in ("exited", "created", "dead"):
            raise ValueError("A service is still changing state. Try recovery again.")
        if service == "db":
            if db is not None:
                raise ValueError("The interrupted trip has more than one database container.")
            db = container
    backup = folder / BACKUP
    existing_archive = backup.is_file()
    report = {
        "version": 1,
        "trip": folder.name,
        "project": project,
        "retained_volumes": sorted(retained),
        "cleanup_verified": False,
        "backup_saved": False,
    }
    if "backup_sha256" in previous:
        report["backup_sha256"] = previous["backup_sha256"]
    save_receipt(folder, report)
    if running:
        docker("stop", "--time", "10", *running)
    if existing_archive and db is None:
        if backup.is_symlink() or previous.get("backup_sha256") != archive_sha256(backup):
            report["backup_saved"] = False
            report["backup_warning"] = "The saved recovery archive needs inspection."
            save_receipt(folder, report)
            raise ValueError("The saved archive cannot be validated without its database owner.")
        report.update(backup_saved=True, backup_sha256=previous["backup_sha256"])
    if db:
        report["backup_saved"] = False
        try:
            report["backup_saved"] = dump_database(db, folder)
            if report["backup_saved"] is not True:
                raise ValueError("The recording archive was not verified.")
            report["backup_sha256"] = archive_sha256(backup)
        except (OSError, ValueError, subprocess.SubprocessError):
            report["backup_saved"] = False
            report["backup_warning"] = (
                "The archive is unavailable. Stopped database resources were retained for retry."
            )
            save_receipt(folder, report)
            raise ValueError(
                "Recording recovery failed; owned resources remain for retry."
            ) from None
        save_receipt(folder, report)
    if ids:
        docker("container", "rm", *ids)
    networks = resources(project, "network")
    if networks:
        docker("network", "rm", *networks)
    if resources(project, "container") or resources(project, "network"):
        raise ValueError("The interrupted trip still owns a service or network.")
    if not retained.issubset(docker("volume", "ls", "-q").decode().split()):
        raise ValueError("Could not verify that all of the trip's volumes were retained.")
    report["cleanup_verified"] = True
    save_receipt(folder, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("identity")
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if not RUN_ID.fullmatch(args.identity):
        parser.error("Select an interrupted trip.")
    folder = ROOT / "evidence/energy-v2" / ("live-journey-" + args.identity)
    if folder.resolve() != folder or not folder.is_dir():
        parser.error("The trip evidence folder is unavailable.")
    try:
        if args.worker:
            release_trip(folder)
            return 0
        elif os.name == "nt":
            import msvcrt

            with (ROOT / ".runtime/p6-monitoring.lock").open("a+b") as lock:
                if lock.tell() == 0:
                    lock.write(b"\0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                try:
                    subprocess.run(
                        [
                            sys.executable,
                            "scripts/linux.py",
                            "--timeout",
                            "300",
                            "--",
                            "python3",
                            "scripts/launcher/recover_run.py",
                            args.identity,
                            "--worker",
                        ],
                        cwd=ROOT,
                        timeout=350,
                        check=True,
                    )
                finally:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            parser.error("Start trip recovery from the Windows launcher.")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Could not verify trip recovery: {exc}", file=sys.stderr)
        return 1
    print("Trip services released; retained volumes verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
