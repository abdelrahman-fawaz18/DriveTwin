"""Windows orchestration for the existing scoped temporary disk provisioner."""

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_ready(ready, identity, catalog, encoded_limit):
    require(isinstance(ready, dict), "Storage readiness must be an object")
    owner = ready.get("owner")
    binding = ready.get("binding", {})
    queue = binding.get("queue") if isinstance(binding, dict) else None
    require(
        ready.get("version") == "temporary-vehicle-storage/1.0.0"
        and isinstance(owner, str)
        and re.fullmatch(r"evdt-disk-[0-9a-f]{32}", owner)
        and ready.get("volumes")
        == {role: owner + "-" + role for role in ("backing", "telemetry", "critical")}
        and isinstance(binding, dict)
        and binding.get("identity") == identity
        and binding.get("owner") == owner
        and binding.get("version") == "vehicle-queue-storage/1.0.0"
        and binding.get("mode") == ("nominal" if encoded_limit == 32 * 1024**2 else "reduced-test")
        and isinstance(queue, dict)
        and queue.get("catalog_version") == catalog
        and type(queue.get("encoded_limit_bytes")) is int
        and queue["encoded_limit_bytes"] == encoded_limit,
        "Storage readiness identity/settings differ",
    )
    return owner


class StorageLease:
    def __init__(
        self,
        settings,
        output,
        runtime,
        identity,
        image,
        *,
        catalog="1.4.0",
        encoded_limit=32 * 1024**2,
        seconds=600,
    ):
        self.output, self.runtime = output.resolve(), runtime.resolve()
        require(
            self.output.is_relative_to(ROOT / "evidence/p1")
            and self.runtime.is_relative_to(ROOT / ".runtime/bounded-storage"),
            "Storage lease paths must stay inside the canonical private fixture roots",
        )
        require(
            isinstance(identity, dict)
            and set(identity) == {"run_id", "asset_id", "source_id"}
            and str(uuid.UUID(identity["run_id"])) == identity["run_id"]
            and all(
                isinstance(identity[k], str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", identity[k])
                for k in ("asset_id", "source_id")
            ),
            "Storage lease identity differs",
        )
        require(
            isinstance(image, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", image)
            and isinstance(catalog, str)
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", catalog)
            and type(seconds) is int
            and 1 <= seconds <= 3600
            and type(encoded_limit) is int
            and 1 <= encoded_limit <= 32 * 1024**2,
            "Storage lease image, catalog, lifetime or capacity differs",
        )
        self.settings, self.identity, self.image = settings, dict(identity), image
        self.catalog, self.encoded_limit, self.seconds = catalog, encoded_limit, seconds
        self.control = self.runtime / "lease"
        self.child = None
        self.ready = None
        self.log = None
        self.linux_root = (
            (
                settings["windows_mount_root"].rstrip("/")
                + "/"
                + ROOT.drive[0].lower()
                + "/"
                + "/".join(ROOT.parts[1:])
            )
            if os.name == "nt"
            else None
        )

    def start(self):
        require(os.name == "nt", "Start the scoped storage lease from Windows")
        require(
            not self.control.exists() and not (self.output / "services").exists(),
            "Refusing an existing storage lease/control output",
        )
        self.output.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)
        identity_file = self.runtime / "identity.json"
        if identity_file.exists() or identity_file.is_symlink():
            require(
                identity_file.is_file()
                and not identity_file.is_symlink()
                and identity_file.stat().st_size <= 1024
                and json.loads(identity_file.read_bytes()) == self.identity,
                "Refusing a changed existing storage identity file",
            )
        else:
            identity_file.write_text(
                json.dumps(self.identity) + "\n", encoding="utf-8", newline="\n"
            )
        self.log = (self.output / "worker.log").open("w", encoding="utf-8")
        argv = [
            "wsl.exe",
            "--distribution",
            self.settings["distribution"],
            "--user",
            "root",
            "--cd",
            self.linux_root,
            "--exec",
            "python3",
            "scripts/monitoring/qualify_disk_boundaries.py",
            "--critical-fault",
            "--queue-image",
            self.image,
            "--evidence-dir",
            (self.output.relative_to(ROOT) / "services").as_posix(),
            "--serve-control",
            self.control.relative_to(ROOT).as_posix(),
            "--vehicle-identity",
            identity_file.relative_to(ROOT).as_posix(),
            "--catalog-version",
            self.catalog,
            "--encoded-limit",
            str(self.encoded_limit),
            "--serve-seconds",
            str(self.seconds),
        ]
        self.child = subprocess.Popen(argv, cwd=ROOT, stdout=self.log, stderr=self.log)
        deadline = time.monotonic() + 150
        ready_file = self.control / "ready.json"
        while not ready_file.is_file():
            require(
                self.child.poll() is None and time.monotonic() < deadline,
                "Owned storage did not become ready; inspect private worker.log",
            )
            time.sleep(0.25)
        require(
            not ready_file.is_symlink() and ready_file.stat().st_size <= 16384,
            "Storage readiness file is linked or oversized",
        )
        ready = json.loads(ready_file.read_bytes())
        validate_ready(ready, self.identity, self.catalog, self.encoded_limit)
        self.ready = ready
        (self.runtime / "binding.json").write_text(
            json.dumps(ready["binding"]) + "\n", encoding="utf-8", newline="\n"
        )
        return ready

    def release_owner(self):
        state_path = self.output / "services/state.json"
        if not state_path.exists():
            return None
        require(
            not state_path.is_symlink() and state_path.stat().st_size <= 1024**2,
            "Storage state file is linked or oversized",
        )
        state = json.loads(state_path.read_bytes())
        owner = state.get("owner")
        require(
            isinstance(owner, str)
            and re.fullmatch(r"evdt-disk-[0-9a-f]{32}", owner)
            and state.get("fault_identity") == self.identity
            and (self.ready is None or self.ready["owner"] == owner),
            "Refusing storage release for a changed identity/owner",
        )
        return owner

    def close(self):
        try:
            if self.child is None:
                return None
            owner = self.release_owner()
            if owner is not None and self.control.is_dir():
                temporary = self.control / "release.tmp"
                temporary.write_text(
                    json.dumps({"owner": owner}) + "\n", encoding="utf-8", newline="\n"
                )
                temporary.replace(self.control / "release.json")
            code = self.child.wait(timeout=180)
            report = json.loads((self.output / "services/qualification.json").read_bytes())
            require(
                code == 0 and report.get("queue_application_image_id") == self.image,
                "Storage worker failed or reported a different exact image",
            )
            return report
        finally:
            if self.log is not None:
                self.log.close()
