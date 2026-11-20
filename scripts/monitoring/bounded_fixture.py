"""Attach existing owned bounded volumes before the shared vehicle fixture starts."""

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def atomic(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value) + "\n", encoding="utf-8")
    temporary.replace(path)


def attach(compose, execute, prefix, runtime, env, record, control, specs):
    image = execute(["docker", "image", "inspect", env["MONITORING_IMAGE"], "--format", "{{.Id}}"])
    request = {
        "version": "bounded-fixture-request/1.0.0",
        "run_id": env["EVDT_RUN_ID"],
        "project": prefix[prefix.index("-p") + 1],
        "image_id": image,
        "runtime": runtime.relative_to(ROOT).as_posix(),
        "specs": specs,
    }
    atomic(control / "request.json", request)
    response_file = control / "response.json"
    deadline = time.monotonic() + 240
    while not response_file.is_file():
        require(time.monotonic() < deadline, "Bounded-storage controller did not answer")
        time.sleep(0.25)
    require(
        not response_file.is_symlink() and response_file.stat().st_size <= 65536,
        "Bounded-storage response is linked or oversized",
    )
    response = json.loads(response_file.read_bytes())
    require(
        response.get("version") == "bounded-fixture-response/1.0.0"
        and response.get("run_id") == request["run_id"]
        and response.get("image_id") == image
        and len(response.get("assets", [])) == len(specs),
        "Bounded-storage response scope differs",
    )
    definitions = {"services": {}, "volumes": {}}
    for spec, supplied in zip(specs, response["assets"]):
        asset = spec["asset_id"]
        require(supplied.get("asset_id") == asset, "Bounded-storage asset order differs")
        ready = supplied["ready"]
        owner = ready["owner"]
        import re

        require(
            re.fullmatch(r"evdt-disk-[0-9a-f]{32}", owner)
            and ready["volumes"]
            == {r: owner + "-" + r for r in ("backing", "telemetry", "critical")}
            and ready["binding"]["identity"]
            == {"run_id": request["run_id"], "asset_id": asset, "source_id": spec["source_id"]}
            and ready["binding"]["queue"]["catalog_version"] == spec["catalog_version"]
            and ready["binding"]["queue"]["encoded_limit_bytes"] == spec["encoded_limit_bytes"],
            "Bounded-storage identity, queue settings or owned volume names differ",
        )
        relative = Path(supplied["binding_path"])
        binding = (ROOT / relative).resolve()
        require(
            not relative.is_absolute()
            and ".." not in relative.parts
            and binding.is_relative_to(ROOT / ".runtime/bounded-storage")
            and binding.is_file(),
            "Binding path is outside the canonical private storage root",
        )
        require(
            json.loads(binding.read_bytes()) == ready["binding"], "Supplied binding bytes differ"
        )
        keys = {role: "bounded-" + role + "-" + asset for role in ("telemetry", "critical")}
        for role, key in keys.items():
            definitions["volumes"][key] = {"external": True, "name": ready["volumes"][role]}
        for service in spec["services"]:
            require(
                re.fullmatch(r"[a-z][a-z0-9-]{0,63}", service),
                "Invalid bounded-storage service name",
            )
            definitions["services"][service] = {
                "environment": {
                    "EVDT_STORAGE_BINDING_FILE": "/run/storage/binding.json",
                    "CATALOG_VERSION": spec["catalog_version"],
                },
                "volumes": [
                    {"type": "volume", "source": keys["telemetry"], "target": "/spool"},
                    {"type": "volume", "source": keys["critical"], "target": "/critical"},
                    {
                        "type": "bind",
                        "source": str(binding),
                        "target": "/run/storage/binding.json",
                        "read_only": True,
                    },
                ],
            }
    override = runtime / "bounded.compose.json"
    override.write_text(json.dumps(definitions, indent=2) + "\n", encoding="utf-8")
    prefix.extend(["-f", str(override)])
    merged = json.loads(compose("config", "--format", "json"))
    for spec in specs:
        for service in spec["services"]:
            selected = merged["services"][service]
            mounts = {item["target"]: item for item in selected["volumes"]}
            require(
                selected["environment"]["EVDT_STORAGE_BINDING_FILE"] == "/run/storage/binding.json"
                and mounts["/spool"]["source"] == "bounded-telemetry-" + spec["asset_id"]
                and mounts["/critical"]["source"] == "bounded-critical-" + spec["asset_id"]
                and mounts["/run/storage/binding.json"]["read_only"] is True,
                "Actual merged Compose storage configuration differs",
            )
    record["bounded_storage"] = {
        "request": request,
        "response": response,
        "merged_mounts_checked": True,
    }
    return definitions
