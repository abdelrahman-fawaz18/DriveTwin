"""Owned disposable database resources for bounded qualification sessions."""

import hashlib
import json
import secrets
import subprocess
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]


class DatabaseSession:
    def __init__(self, output):
        self.output = output
        self.project = "evdt-e2c-" + hashlib.sha256(output.name.encode()).hexdigest()[:12]
        self.runtime = ROOT / ".runtime/energy-v2" / self.project
        self.runtime.mkdir(parents=True, exist_ok=False)
        self.credentials = {}
        for role in ("admin", "ingest", "read", "registry", "replay", "forecast"):
            path = self.runtime / (role + "_db")
            path.write_text(secrets.token_urlsafe(36) + "\n")
            self.credentials[role] = path
        self.label = "com.docker.compose.project=" + self.project
        self.log = (output / "database-commands.log").open("xb")
        self.started = []
        self.host = None

    def docker(self, *args, input_bytes=None, binary=False, timeout=60, check=True):
        result = subprocess.run(
            ["docker", *args], cwd=ROOT, capture_output=True, input=input_bytes, timeout=timeout
        )
        if not binary:
            self.log.write(result.stdout)
        self.log.write(result.stderr + b"\n")
        self.log.flush()
        if check and result.returncode:
            raise RuntimeError(f"Docker {args[0]} failed; inspect the private database log")
        return result

    def start(self, suffix="primary"):
        image = json.loads((ROOT / "environments/dataflow-images.json").read_bytes())["postgres"][
            "digest"
        ]
        name = self.project + "-" + suffix
        network = self.project + "-data"
        if not self.started:
            self.docker("network", "create", "--internal", "--label", self.label, network)
        volume = name + "-data"
        self.docker("volume", "create", "--label", self.label, volume)
        self.docker(
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            self.label,
            "--label",
            "com.docker.compose.service=db",
            "--label",
            "com.docker.compose.oneoff=False",
            "--network",
            network,
            "--memory",
            "256m",
            "--memory-swap",
            "256m",
            "--volume",
            volume + ":/var/lib/postgresql",
            "--volume",
            str(self.credentials["admin"]) + ":/run/secrets/admin_db:ro",
            "--env",
            "POSTGRES_USER=evdt_admin",
            "--env",
            "POSTGRES_DB=evdt",
            "--env",
            "POSTGRES_PASSWORD_FILE=/run/secrets/admin_db",
            image,
            "postgres",
            "-c",
            "shared_buffers=32MB",
            "-c",
            "max_connections=20",
        )
        self.started.append(name)
        info = json.loads(self.docker("inspect", name).stdout)[0]
        self.host = info["NetworkSettings"]["Networks"][network]["IPAddress"]
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                with self.connect() as conn:
                    conn.execute("SELECT 1")
                return name
            except psycopg.OperationalError:
                time.sleep(0.25)
        raise RuntimeError("Temporary database readiness deadline exceeded")

    def connect(self, role="admin"):
        return psycopg.connect(
            host=self.host,
            dbname="evdt",
            user="evdt_" + role,
            password=self.credentials[role].read_text().strip(),
            connect_timeout=3,
            row_factory=dict_row,
        )

    def environment(self, role="admin"):
        return {
            "DB_USER": "evdt_" + role,
            "DB_HOST": self.host,
            "DB_PASSWORD_FILE": str(self.credentials[role]),
            **{
                name.upper() + "_DB_PASSWORD_FILE": str(path)
                for name, path in self.credentials.items()
            },
        }

    def finish(self):
        # Query only labels assigned by this fixture. Preserve all unrelated resources.
        try:
            for kind in ("container", "volume", "network"):
                listing = [kind, "ls", "-q", "--filter", "label=" + self.label]
                if kind == "container":
                    listing.append("-a")
                ids = self.docker(*listing).stdout.decode().split()
                if ids:
                    self.docker(kind, "rm", *(["-f", "-v"] if kind == "container" else []), *ids)
                if self.docker(*listing).stdout.strip():
                    raise RuntimeError(f"Owned {kind} cleanup incomplete")
        finally:
            self.log.close()
