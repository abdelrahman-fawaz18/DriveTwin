"""Seal a private reusable history checkpoint after all source-backed writes finish."""

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from evdt.recovery.history_bundle import SOURCE_FILES, seal, verify  # noqa: E402


def save(compose, execute, prefix, runtime, output, env, record, *, assets, samples):
    secret_dir = Path(env["SECRET_DIRECTORY"])
    readers = json.loads((secret_dir / "readers.json").read_bytes())["readers"]
    token = next(r["token"] for r in readers if r["label"] == "all")
    base = f"http://127.0.0.1:{env['API_PORT']}"
    signals = json.loads((ROOT / "benchmarks/history.json").read_bytes())["query"]["signals"]

    def read(path):
        with urlopen(
            Request(base + path, headers={"Authorization": "Bearer " + token}), timeout=10
        ) as response:
            return json.load(response)

    queries = []
    for asset in dict.fromkeys((assets[0], assets[-1])):
        cursor = read("/v1/snapshot?" + urlencode({"asset_id": asset}))["cursor"]
        query = {
            "asset_id": asset,
            "as_of": cursor,
            "from_s": 0,
            "to_s": min(3600, samples),
            "signals": signals,
        }
        for route, options in [
            ("/v1/plot", {"point_limit": 5000}),
            ("/v1/history-summary", {"bucket_s": 60}),
        ]:
            path = route + "?" + urlencode({**query, **options}, doseq=True)
            queries.append({"path": path, "response": read(path)})
    project = prefix[prefix.index("-p") + 1]
    parent = ROOT / ".private/backups" / project
    parent.mkdir(parents=True)
    partial, final = parent / "history.partial", parent / "history"
    partial.mkdir()
    compose("stop", "registry")
    compose(
        "exec",
        "-T",
        "db",
        "psql",
        "-U",
        "evdt_admin",
        "-d",
        "evdt",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "ALTER DATABASE evdt SET default_transaction_read_only=on",
    )

    def probe(action):
        return compose(
            "run",
            "--rm",
            "--no-deps",
            "probe",
            "python",
            "-m",
            "evdt.recovery.database",
            action,
            timeout=600,
        )

    sealed = json.loads(probe("sealed"))
    if sealed != {"readonly_write_denied": True}:
        raise ValueError("History database seal failed")
    original = json.loads(probe("inventory"))
    cluster = probe("cluster").strip()
    (partial / "inventory.json").write_text(json.dumps(original, sort_keys=True) + "\n")
    (partial / "queries.json").write_text(json.dumps(queries, sort_keys=True) + "\n")
    (partial / "counts.json").write_text(
        json.dumps(record["history_volume"]["verification"], sort_keys=True) + "\n"
    )
    for folder, pattern in [
        ("contracts", "**/*.json"),
        ("catalog", "*.json"),
        ("migrations", "monitoring/*.sql"),
    ]:
        for path in (ROOT / folder).glob(pattern):
            target = partial / "artifacts" / folder / path.relative_to(ROOT / folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    (partial / "run-config").mkdir()
    for name in [
        "run_policy.json",
        "map_context.json",
        *[f"map_context{i}.json" for i in range(2, 11)],
    ]:
        shutil.copyfile(secret_dir / name, partial / "run-config" / name)
    for asset in assets:
        for name in SOURCE_FILES:
            target = partial / "sources" / asset / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(output / asset / name, target)
    # Binary archives never cross a text decoder, shell pipeline or pseudo-terminal.
    argv = [
        *prefix,
        "exec",
        "-T",
        "db",
        "pg_dump",
        "-U",
        "evdt_admin",
        "-d",
        "evdt",
        "--format=custom",
        "--no-owner",
    ]
    with (partial / "database.dump").open("xb") as destination:
        completed = subprocess.run(
            argv, cwd=ROOT, stdout=destination, stderr=subprocess.PIPE, timeout=600
        )
    (output / "history-dump.stderr").write_bytes(completed.stderr)
    record["commands"].append({"argv": argv, "exit_code": completed.returncode})
    if completed.returncode:
        raise RuntimeError("History database dump failed; partial checkpoint preserved")
    if json.loads(probe("inventory")) != original:
        raise RuntimeError("History source changed while creating checkpoint")
    image_id = execute(
        ["docker", "image", "inspect", env["MONITORING_IMAGE"], "--format", "{{.Id}}"]
    )
    manifest = seal(
        partial,
        {
            "scope": "database-history-only",
            "protocol": "history-volume/1.0.0",
            "assets": assets,
            "samples_per_asset": samples,
            "run_id": env["EVDT_RUN_ID"],
            "source_cluster": cluster,
            "application_image_id": image_id,
            "postgres_image": env["POSTGRES_IMAGE"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": execute(["git", "rev-parse", "HEAD"]),
            "source_working_tree_dirty": bool(execute(["git", "status", "--porcelain"])),
            "source_sha256": record["source_sha256"]
            if "source_sha256" in record
            else {
                p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in ROOT.glob("src/**/*.py")
            },
            "consistency": "all source/load processes finished, registry stopped, database sealed read-only",
            "excluded": "credentials, account data, vehicle queues and physical plant resumption",
        },
    )
    partial.rename(final)
    verify(final, image_id)
    return {
        "passed": True,
        "format": manifest["format"],
        "path": final.relative_to(ROOT).as_posix(),
        "files": len(manifest["files"]),
        "bytes": sum(r["size"] for r in manifest["files"].values()),
        "manifest_sha256": hashlib.sha256((final / "manifest.json").read_bytes()).hexdigest(),
        "queries": len(queries),
        "source_sealed": True,
        "restore_passed": False,
    }
