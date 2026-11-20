"""Small controlled history semantics; distinct from full W2 volume and performance."""

import json
from urllib.request import urlopen

from workload_source_fixture import require, wait


def qualify(compose, execute, prefix, runtime, output, env, record):
    result = {"passed": False, "scope": "E3-A bounded history SQL/API semantics; not W2/Q05/Q10"}
    record["history_backend"] = result
    policy_file = runtime / "secrets/run_policy.json"
    policy = json.loads(policy_file.read_bytes())
    policy["sources"][0]["expected"]["paced"] = False
    policy_file.write_text(json.dumps(policy, sort_keys=True) + "\n")
    override = runtime / "history.compose.json"
    override.write_text(
        json.dumps(
            {
                "services": {
                    "source": {
                        "command": [
                            "python",
                            "-m",
                            "evdt.monitoring.adapter",
                            "--maximum-samples",
                            "30",
                            "--accelerated",
                        ]
                    }
                }
            }
        )
        + "\n"
    )
    prefix.extend(["-f", str(override)])
    compose("config", "--quiet")
    compose(
        "up", "-d", "db", "broker", "migrate", "registry", "ingest", "api", "sender", timeout=120
    )

    def ready():
        with urlopen(f"http://127.0.0.1:{env['API_PORT']}/health/ready", timeout=3) as response:
            return json.load(response)["ready"]

    wait(ready)
    compose("up", "-d", "source", timeout=120)
    require(
        execute(["docker", "wait", compose("ps", "-aq", "source")], timeout=150) == "0",
        "Baseline source failed",
    )
    raw = compose(
        "run",
        "--rm",
        "--no-deps",
        "-v",
        str(output) + ":/history-evidence",
        "probe",
        "python",
        "-m",
        "evdt.monitoring.history_probe",
        "--output",
        "/history-evidence",
        timeout=180,
    )
    lines = [
        line.split("=", 1)[1]
        for line in raw.splitlines()
        if line.startswith("HISTORY_BACKEND_RESULT=")
    ]
    require(len(lines) == 1, "History probe result missing")
    result["checks"] = json.loads(lines[0])
    result["passed"] = result["checks"]["passed"]
    return result
