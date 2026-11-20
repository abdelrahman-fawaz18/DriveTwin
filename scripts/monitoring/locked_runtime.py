"""Build the existing locked production runtime through the normal WSL launcher."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build_runtime(output, tag):
    pins = json.loads((ROOT / "environments/dataflow-images.json").read_bytes())
    pins.update(json.loads((ROOT / "environments/engineering-images.json").read_bytes()))
    argv = [
        sys.executable,
        "scripts/linux.py",
        "--timeout",
        "1200",
        "--",
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "-f",
        "containers/monitoring.Dockerfile",
        "--target",
        "runtime",
    ]
    for name in ("python", "uv", "node"):
        argv.extend(["--build-arg", name.upper() + "_IMAGE=" + pins[name]["digest"]])
    argv.extend(["-t", tag, "."])
    with (output / "build.log").open("w", encoding="utf-8") as log:
        subprocess.run(argv, cwd=ROOT, stdout=log, stderr=log, timeout=1260, check=True)
    return subprocess.check_output(
        [
            sys.executable,
            "scripts/linux.py",
            "--",
            "docker",
            "image",
            "inspect",
            tag,
            "--format",
            "{{.Id}}",
        ],
        cwd=ROOT,
        text=True,
    ).strip()
