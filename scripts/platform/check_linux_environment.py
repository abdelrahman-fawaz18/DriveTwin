"""Check Ubuntu tooling before the desktop app's agent-environment switch.

Checks cover the configured Ubuntu account, tools, and checkout access.
Run as the regular Linux user, with an output path inside the project.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import venv


def run_command(args: list[str], timeout: int = 30) -> dict:
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
        return {
            "command": args,
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": args, "exit_code": None, "error": str(error)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    if not output.is_relative_to(root):
        parser.error("The diagnostic report must be saved inside the project.")
    if platform.system() != "Linux":
        parser.error("Run this diagnostic inside Ubuntu, not Windows Python.")

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": str(uuid.uuid4()),
        "scope": "Linux tooling through WSL; Ubuntu runtime preflight",
        "user": getpass.getuser(),
        "uid": os.getuid(),
        "distribution": os.environ.get("WSL_DISTRO_NAME"),
        "kernel": platform.release(),
        "project_root": str(root),
        "working_directory": str(Path.cwd()),
        "versions": {},
        "checks": {},
    }
    checks = report["checks"]
    checks["regular_linux_user"] = os.getuid() != 0
    checks["expected_distribution"] = report["distribution"] == "Ubuntu-24.04"
    checks["working_directory_is_project"] = Path.cwd().resolve() == root
    checks["plan_readable"] = (root / "SCOPE.md").read_text(
        encoding="utf-8-sig"
    ).startswith("# Electric Bus Digital Twin")
    with tempfile.NamedTemporaryFile(mode="w+", dir=root, encoding="utf-8") as probe:
        probe.write(report["run_id"])
        probe.flush()
        probe.seek(0)
        checks["project_file_roundtrip"] = probe.read() == report["run_id"]

    commands = {
        "git": ["git", "--version"],
        "python": [sys.executable, "--version"],
        "pip": [sys.executable, "-m", "pip", "--version"],
        "curl": ["curl", "--version"],
        "compiler": ["gcc", "--version"],
        "make": ["make", "--version"],
        "node": ["node", "--version"],
        "npm": ["npm", "--version"],
        "bubblewrap": ["bwrap", "--version"],
    }
    for name, command in commands.items():
        result = run_command(command)
        executable = shutil.which(command[0])
        result["executable"] = executable
        result["resolved_executable"] = str(Path(executable).resolve()) if executable else None
        report["versions"][name] = result
        checks[f"{name}_available_in_linux"] = (
            result["exit_code"] == 0
            and executable is not None
            and not str(Path(executable).resolve()).startswith("/mnt/")
        )

    node_probe = run_command(
        ["node", "-e", "if(process.platform!=='linux')process.exit(1); console.log(process.platform)"]
    )
    report["node_platform"] = node_probe
    checks["node_executes_in_linux"] = node_probe["exit_code"] == 0
    for expected in (0, 7):
        result = run_command([sys.executable, "-c", f"raise SystemExit({expected})"])
        report[f"exit_{expected}_probe"] = result
        checks[f"subprocess_exit_{expected}_preserved"] = result["exit_code"] == expected

    cache = Path.home() / ".cache" / "ev-dt" / "p0"
    cache.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="venv-check-", dir=cache) as temp:
            venv.EnvBuilder(with_pip=True).create(temp)
            venv_python = str(Path(temp) / "bin" / "python")
            result = run_command([venv_python, "-m", "pip", "--version"])
            report["temporary_virtual_environment"] = result
            checks["isolated_python_environment"] = result["exit_code"] == 0
    except (OSError, subprocess.SubprocessError) as error:
        report["temporary_virtual_environment"] = {"error": str(error)}
        checks["isolated_python_environment"] = False

    # This creates a short-lived isolated process; it does not change host policy.
    bwrap = run_command([
        "bwrap", "--unshare-user", "--unshare-pid", "--unshare-net",
        "--die-with-parent", "--ro-bind", "/", "/", "--proc", "/proc",
        "--dev", "/dev", "--", "/usr/bin/true",
    ])
    report["bubblewrap_namespace_probe"] = bwrap
    checks["bubblewrap_process_creation"] = bwrap["exit_code"] == 0

    try:
        request = urllib.request.Request("https://pypi.org/simple/pip/", method="HEAD")
        with urllib.request.urlopen(request, timeout=20) as response:
            report["package_network"] = {"url": request.full_url, "http_status": response.status}
            checks["python_package_https"] = response.status == 200
    except (OSError, ValueError) as error:
        report["package_network"] = {"error": str(error)}
        checks["python_package_https"] = False

    report["memory"] = run_command(["free", "-b"])
    report["all_setup_checks_passed"] = all(checks.values())
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "checks": checks}, indent=2))
    return 0 if report["all_setup_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
