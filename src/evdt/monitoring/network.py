"""One reviewed SUMO network import used by motion and static map preparation."""

import json
import subprocess
from pathlib import Path


def build_network(scenario: Path, osm: Path, work: Path):
    import sumo

    binary = Path(sumo.SUMO_HOME) / "bin"
    work.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(binary / "netconvert"),
            "--osm-files",
            str(osm),
            "--output-file",
            str(work / "network.net.xml"),
            "--keep-edges.by-vclass",
            "bus",
            "--junctions.join",
            "true",
            "--output.original-names",
            "true",
            "--remove-edges.isolated",
            "true",
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    (work / "netconvert.log").write_text(result.stdout + result.stderr)
    review = json.loads((scenario / "import-review.json").read_text())
    from collections import Counter

    messages = [
        line
        for line in (result.stdout + result.stderr).splitlines()
        if line.startswith(("Warning:", "Error:"))
    ]
    if result.returncode or Counter(messages) != Counter(review["expected_messages"]):
        raise RuntimeError("Motion-source import differs from the reviewed map fixture")
    return work / "network.net.xml"
