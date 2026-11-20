"""Restore the previously accepted archive and read its unmodified legacy records."""

import json
import shutil
import subprocess

from energy_holdout import sha
from energy_runtime import ROOT


def qualify_legacy(compose, admin, wait_for, require, output):
    reviewed = json.loads((ROOT / "docs/verification/energy-product.json").read_bytes())
    old = ROOT / reviewed["full_integration_private_evidence"]
    expected = [
        json.loads((old / name / "record.json").read_bytes())
        for name in ("prediction", "unavailable")
    ]
    require(all("extensions" not in r for r in expected), "Legacy fixture already has extensions")
    archive = old / "database.dump"
    # Keep just two database services resident. All are owned by the parent fixture.
    compose("stop", "db-restore")
    compose("up", "-d", "db-legacy")
    wait_for(
        lambda: (
            compose(
                "exec",
                "-T",
                "db-legacy",
                "pg_isready",
                "-U",
                "evdt_admin",
                "-d",
                "evdt",
                check=False,
            ).returncode
            == 0
        )
    )
    source_cluster = admin("db", "evdt.recovery.database", "cluster").stdout.decode().strip()
    admin("db-legacy", "evdt.recovery.database", "bootstrap", "--source-cluster", source_cluster)
    compose(
        "exec",
        "-T",
        "db-legacy",
        "pg_restore",
        "-U",
        "evdt_admin",
        "-d",
        "evdt",
        "--exit-on-error",
        raw=archive.read_bytes(),
    )
    audit = json.loads(admin("db-legacy", "evdt.monitoring.energy_probe", "audit").stdout)
    require(
        audit == {"records": 2, "available": 1, "unavailable": 1, "original_packets_intact": 61},
        "Legacy archive accounting changed",
    )
    script = "from evdt.monitoring.energy_views import read; from fastapi.encoders import jsonable_encoder; import json; print(json.dumps(jsonable_encoder(read('bus-001'))))"
    response = json.loads(
        compose(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "-e",
            "DB_HOST=db-legacy",
            "-e",
            "DB_USER=evdt_read",
            "-e",
            "DB_PASSWORD_FILE=/run/secrets/read_db",
            "-e",
            "EVDT_RUN_ID=" + expected[0]["run_id"],
            "probe",
            "python",
            "-c",
            script,
        ).stdout
    )
    require(
        [r["payload"] for r in reversed(response["items"])] == expected,
        "Legacy immutable records changed on restore/read",
    )
    require(
        response["status"] == "unavailable"
        and response["values"] is None
        and response["extensions"] is None,
        "Old readings were presented as fresh",
    )
    file = output / "legacy-read.json"
    file.write_text(json.dumps(response), encoding="utf-8")
    module = (ROOT / "apps/dashboard/src/features/forecast/energyModel.ts").as_uri()
    check = f"import {{parseEnergy}} from {json.dumps(module)}; import fs from 'node:fs'; const x=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));parseEnergy(x,x.run_id,x.asset_id);"
    result = subprocess.run(
        [
            shutil.which("node.exe"),
            "--experimental-strip-types",
            "--input-type=module",
            "-e",
            check,
            str(file),
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=20,
    )
    (output / "legacy-client.log").write_bytes(result.stdout + result.stderr)
    require(result.returncode == 0, "Current browser parser rejected the actual legacy response")
    return {
        "passed": True,
        "fresh_cluster": True,
        "legacy_archive_sha256": sha(archive),
        "exact_original_payloads": 2,
        "original_packets_intact": 61,
        "current_read_role_and_browser_parser": True,
        "old_records_rewritten": False,
    }
