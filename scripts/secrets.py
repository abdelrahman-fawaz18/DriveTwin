"""Install a pinned local scanner or scan only Git candidate/index files."""

import argparse
import hashlib
import io
import json
import os
import platform
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from privacy_guard import ROOT, candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "scan"])
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    if platform.machine().lower() not in ("amd64", "x86_64"):
        parser.error("This lock qualifies x64 Windows/Linux only")
    lock = json.loads((ROOT / "environments/secret-scanner.json").read_text())
    spec = lock["windows" if os.name == "nt" else "linux"]
    cache = ROOT / ".runtime/tools/gitleaks" / lock["version"]
    cache.mkdir(parents=True, exist_ok=True)
    executable_name = "gitleaks.exe" if os.name == "nt" else "gitleaks"
    executable = cache / executable_name
    archive = cache / spec["asset"]
    if args.action == "install" and not archive.exists():
        url = lock["source"].replace("/tag/", "/download/") + "/" + spec["asset"]
        request = urllib.request.Request(url, headers={"User-Agent": "EV-DT-setup"})
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read(50_000_001)
        if len(payload) > 50_000_000 or hashlib.sha256(payload).hexdigest() != spec["sha256"]:
            raise SystemExit("Scanner archive checksum/size mismatch")
        archive.write_bytes(payload)
    if not archive.exists():
        raise SystemExit("Run python scripts/secrets.py install first (one network download).")
    payload = archive.read_bytes()
    if hashlib.sha256(payload).hexdigest() != spec["sha256"]:
        raise SystemExit("Cached scanner archive checksum mismatch")
    # Extract only the fixed executable name, never arbitrary archive paths.
    if os.name == "nt":
        with zipfile.ZipFile(io.BytesIO(payload)) as package:
            binary = package.read(executable_name)
    else:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as package:
            binary = package.extractfile(executable_name).read()
    if not executable.exists() or executable.read_bytes() != binary:
        executable.write_bytes(binary)
        executable.chmod(0o755)
    if args.action == "install":
        print(f"Verified project-local Gitleaks {lock['version']}; no system settings changed.")
        return 0
    common = [str(executable), "--redact=100", "--no-banner", "--exit-code=1"]
    # JSON report and scanner output stay private, including paths and match metadata.
    report = cache / "last-scan.json"
    report.unlink(missing_ok=True)
    if args.history:
        command = [*common, "--report-format=json", f"--report-path={report}", "git", str(ROOT)]
        result = subprocess.run(command, capture_output=True, cwd=ROOT)
    else:
        with tempfile.TemporaryDirectory(prefix="snapshot-", dir=cache) as directory:
            snapshot = Path(directory)
            for name in candidates(staged=args.staged):
                path = ROOT / name
                if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
                    raise SystemExit("Refusing snapshot of symlink/outside-root path")
                if not args.staged and not path.exists():
                    continue
                data = (
                    subprocess.run(
                        ["git", "show", ":" + name], cwd=ROOT, check=True, capture_output=True
                    ).stdout
                    if args.staged
                    else path.read_bytes()
                )
                target = snapshot / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            command = [*common, "--report-format=json", f"--report-path={report}", "dir", directory]
            result = subprocess.run(command, capture_output=True, cwd=ROOT)
    findings = json.loads(report.read_text()) if report.exists() else None
    print(
        f"Gitleaks {lock['version']}: exit {result.returncode}; "
        f"{len(findings) if isinstance(findings, list) else 'unknown'} findings. "
        "Detailed output stays in ignored .runtime/tools/gitleaks."
    )
    if result.returncode:
        (cache / "last-scan.log").write_bytes(result.stdout + result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
