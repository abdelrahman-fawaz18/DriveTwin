"""Provision the isolated, explicitly selected moving-charge development profile."""

from live_inputs import read, write

from evdt.energy.route_aware.wire import canonical_bytes, fingerprint


def configure(
    services,
    config,
    runtime,
    scenario,
    assignment,
    image,
    *,
    recovery=False,
    outage=None,
    automatic=False,
    queue_trial=False,
):
    directory = runtime / "secrets"
    assignment_file = directory / "moving-charge-assignment.json"
    assignment_file.write_bytes(canonical_bytes(assignment) + b"\n")
    prepared = scenario / "prepared-journey.json"
    write(
        prepared,
        {
            **read(prepared),
            "moving_charge_assignment_sha256": fingerprint(assignment),
            **({"moving_charge_recovery": True} if recovery else {}),
            **({"qualification_charge_queue": True} if queue_trial else {}),
            **({"qualification_charger_outage": outage} if outage else {}),
        },
    )
    assignment_mount = f"{assignment_file}:/run/evdt/moving-charge-assignment.json:ro"
    assignment_env = {"EVDT_MOVING_CHARGE_ASSIGNMENT": "/run/evdt/moving-charge-assignment.json"}
    source = services["source"]
    source["volumes"] += [
        assignment_mount,
        f"{directory / 'command_executor'}:/run/secrets/command_executor:ro",
    ]
    source["environment"].update(
        **assignment_env,
        EVDT_MOVING_CHARGE_EXECUTOR_KEY="/run/secrets/command_executor",
    )
    config.setdefault("secrets", {}).update(
        {
            name: {"file": str(directory / name)}
            for name in ("command_db", "command_operator", "command_executor", "command_observer")
        }
    )
    app = {
        "image": image,
        "pull_policy": "never",
        "read_only": True,
        "tmpfs": ["/tmp:size=32m,mode=1777"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": "192m",
        "pids_limit": 48,
        "networks": ["fixture"],
        "working_dir": "/workspace",
        "volumes": [
            f"./{path}:/workspace/{path}:ro" for path in ("src", "contracts", "migrations")
        ],
    }
    environment = {
        "PYTHONPATH": "/workspace/src",
        "EVDT_RUN_ID": assignment.run_id,
        "COMMAND_ASSET_ID": assignment.asset_id,
        "COMMAND_ISSUER": "operator",
        "EVDT_MOVING_CHARGE_COMMANDS": "1",
        **({"EVDT_MOVING_CHARGE_RECOVERY": "1"} if recovery else {}),
        **({"EVDT_MOVING_CHARGE_AUTOMATIC": "1"} if automatic else {}),
        **({"EVDT_MOVING_CHARGE_QUEUE_TRIAL": "1"} if queue_trial else {}),
    }
    command_secrets = ["command_db", "command_operator", "command_executor", "command_observer"]
    if automatic:
        from secrets import token_urlsafe

        policy = directory / "command_moving_charge_policy"
        with policy.open("x") as handle:
            handle.write(token_urlsafe(36) + "\n")
        config["secrets"][policy.name] = {"file": str(policy)}
        command_secrets.append(policy.name)
    services["command-migrate"] = {
        **app,
        "environment": {
            **environment,
            "DB_USER": "evdt_admin",
            "DB_PASSWORD_FILE": "/run/secrets/admin_db",
            "COMMAND_DB_PASSWORD_FILE": "/run/secrets/command_db",
        },
        "command": ["python", "-m", "evdt.command_journal"],
        "secrets": ["admin_db", "command_db"],
        "depends_on": {"migrate": {"condition": "service_completed_successfully"}},
    }
    services["command-api"] = {
        **app,
        "environment": {
            **environment,
            **assignment_env,
            "DB_USER": "evdt_command",
            "DB_PASSWORD_FILE": "/run/secrets/command_db",
        },
        "volumes": [*app["volumes"], assignment_mount],
        "secrets": command_secrets,
        "command": [
            "uvicorn",
            "evdt.command_api:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--limit-concurrency",
            "16",
        ],
        "depends_on": {"command-migrate": {"condition": "service_completed_successfully"}},
        "healthcheck": {
            "test": [
                "CMD",
                "python",
                "-c",
                "from urllib.request import urlopen;urlopen('http://127.0.0.1:8000/health/ready',timeout=1)",
            ],
            "interval": "2s",
            "timeout": "2s",
            "retries": 15,
        },
    }
