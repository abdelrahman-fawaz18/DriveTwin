"""Exercise the current reader after the original sealed-history restore."""

import io
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from docker_resources import DockerReadClient
from history_download_audit import originals, validate_csv, validate_json, validate_raw_metadata
from qualify_connected_speed_limit import digest, read, require, write

ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT = ROOT / ".private/backups/evdt-e3c-55771e34358e/history"
MANIFEST_SHA256 = "eff2018c31aae9faf2aa241e3da9b1e3ec604f50079c205dd0b6aea2306fc60c"


def verify_artifacts(checkpoint):
    require(digest(checkpoint / "manifest.json") == MANIFEST_SHA256, "Unexpected history seal")
    count = 0
    archived = checkpoint / "artifacts"
    for directory in ("catalog", "contracts", "migrations"):
        for path in (archived / directory).rglob("*"):
            if path.is_file():
                current = ROOT / path.relative_to(archived)
                require(digest(path) == digest(current), "Archived schema or migration changed")
                count += 1
    require(count == 33, "Archived compatibility inventory differs")
    return count


def restore_identity(output):
    identity = read(output / "restore-control.json")
    identifier = identity["runtime_id"]
    require(
        re.fullmatch(r"[0-9a-f]{32}", identifier)
        and identity["project"] == "evdt-history-restore-" + identifier[:12],
        "Unexpected isolated restore identity",
    )
    return identity["project"], ROOT / ".runtime/history-restore" / identifier


class HistoryResourceClient(DockerReadClient):
    def __init__(self, project, output):
        super().__init__(project)
        self.output = output / "restore"
        self.restore_selected = False

    def containers(self):
        if not self.restore_selected and (self.output / "restore-control.json").is_file():
            try:
                self.project, _ = restore_identity(self.output)
                self.restore_selected = True
            except json.JSONDecodeError:
                # The original launcher publishes the file before creating containers.
                pass
        return super().containers()


def check_reader(checkpoint, output, image, compose, base, token, manifest, started):
    from qualify_history_queries import qualify
    from qualify_history_restore import equal_query

    project, runtime = restore_identity(output)
    config = runtime / "compose.json"
    override = runtime / "current-reader.json"
    write(override, {"services": {"api": {"image": image}}})
    with (output / "current-reader-start.log").open("xb") as log:
        subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                str(config),
                "-f",
                str(override),
                "up",
                "-d",
                "--no-deps",
                "--pull",
                "never",
                "api",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=log,
            timeout=45,
            check=True,
        )
    container = compose("ps", "-q", "api")
    observed = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.Image}}", container], cwd=ROOT, timeout=10, text=True
    ).strip()
    require(observed == image, "Restored database is not served by the current reader image")

    def get(path, credential=token, status=200):
        headers = {"Authorization": "Bearer " + credential} if credential else {}
        try:
            response = urlopen(Request(base + path, headers=headers), timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            require(
                response.status == status, f"Unexpected history response status {response.status}"
            )
            body = response.read(8 * 1024 * 1024 + 1)
            require(len(body) <= 8 * 1024 * 1024, "Unbounded current reader response")
            return body

    deadline = time.monotonic() + 30
    while True:
        try:
            if json.loads(get("/health/ready", None))["ready"]:
                break
        except (OSError, RuntimeError, ValueError):
            pass
        require(time.monotonic() < deadline, "Current history reader did not start")
        time.sleep(0.25)

    metadata = manifest["metadata"]
    first, last = metadata["assets"][0], metadata["assets"][-1]
    scoped = read(runtime / "secrets/readers")["readers"][1]["token"]
    get(f"/v1/snapshot?asset_id={first}", scoped)
    get(f"/v1/snapshot?asset_id={last}", scoped, 403)
    get(f"/v1/snapshot?asset_id={first}", None, 401)
    queries = read(checkpoint / "queries.json")
    for query in queries:
        require(
            equal_query(json.loads(get(query["path"])), query["response"]),
            "Current reader changed a sealed representative history response",
        )

    signal = "motion.speed"
    definitions = read(checkpoint / "artifacts/catalog/monitoring-fleet-1.4.0.json")["signals"]
    cursor = json.loads(get(f"/v1/snapshot?asset_id={first}"))["cursor"]
    expected = originals(checkpoint, first, [signal], 0, 15)
    fixture = {
        "run_id": metadata["run_id"],
        "as_of": cursor,
        "from_s": 0,
        "to_s": 15,
        "signals": [signal],
        "expected_rows": 15,
        "definitions": {signal: definitions[signal]},
        "policy_sha256": digest(checkpoint / "run-config/run_policy.json"),
    }
    query = urlencode(
        {"asset_id": first, "as_of": cursor, "signals": signal, "from_s": 0, "to_s": 15}
    )
    validate_json(
        json.loads(get(f"/v1/export?{query}&format=json")),
        expected,
        fixture,
        first,
        metadata_validator=validate_raw_metadata,
    )
    csv_text = get(f"/v1/export?{query}&format=csv").decode("utf-8")
    validate_csv(
        io.StringIO(csv_text), expected, fixture, first, metadata_validator=validate_raw_metadata
    )
    restore_s = time.monotonic() - started
    require(restore_s <= 900, "Restore and current representative queries exceeded 900 seconds")

    result = qualify(checkpoint, output, compose, base, token, manifest)
    return {
        "passed": result["Q05_W2_passed"],
        "current_reader_image": observed,
        "current_scoped_access_checked": True,
        "exact_representative_queries": len(queries),
        "export_rows_per_format": len(expected),
        "restore_and_current_reader_s": restore_s,
        "query_timing": result.get("distributions"),
    }


def run_history(output, image):
    from qualify_history_restore import restore

    artifacts = verify_artifacts(CHECKPOINT)
    restore_output = output / "restore"
    restore_output.mkdir()
    started = time.monotonic()

    def after_restore(compose, base, token, manifest):
        return check_reader(
            CHECKPOINT, restore_output, image, compose, base, token, manifest, started
        )

    result = restore(CHECKPOINT, restore_output, after_restore=after_restore)
    require(
        result["passed"], "Sealed history or current reader failed. See the private restore report."
    )
    return {
        **result["additional_checks"],
        "matching_archived_artifacts": artifacts,
        "restored_application_image_id": result["restored_application_image_id"],
        "restored_events": result["restored_table_counts"]["m_observations"],
        "restore_and_verification_s": result["restore_and_verification_s"],
    }
