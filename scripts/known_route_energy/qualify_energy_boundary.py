"""Bounded whole-mission design and real worker access-denial qualification."""

import argparse
import hashlib
import json
import sys
import uuid
from collections import Counter
from pathlib import Path

from energy_runtime import (
    PACKAGED,
    ROOT,
    build_image,
    docker,
    fingerprints,
    linux_checkout,
    remove_owned,
    run_isolated,
)
from qualify_energy_forecasts import synthetic_packets

sys.path.insert(0, str(ROOT / "src"))
from evdt.energy.forecasts import ForecastClock, ForecastPolicy, MissionRequest  # noqa: E402
from evdt.energy.jobs import make_job, run_request  # noqa: E402
from evdt.energy.model import DrivingInterval, EnergyParameters  # noqa: E402
from evdt.energy.observations import ObservationScope  # noqa: E402
from evdt.energy.partitions import authorize_partition, parse_design  # noqa: E402

TRUTH_SERVER = """
import http.server,json,pathlib
root=pathlib.Path('/private-truth')
names=['actual_capacity','fault_labels','future_traffic','realization_seed']
for name in names:
    (root/(name+'.json')).write_text(json.dumps({'kind':name,'synthetic_private_canary':True}))
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path=root/(self.path.strip('/')+'.json')
        if not path.is_file():
            self.send_error(404)
            return
        data=path.read_bytes()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(data)
    def log_message(self,*args):
        pass
http.server.HTTPServer(('0.0.0.0',8079),Handler).serve_forever()
"""


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.evidence_dir.resolve()
    if output.exists() or not output.is_relative_to(ROOT / "evidence/p2"):
        parser.error("Choose a new evidence/p2 directory")
    output.mkdir(parents=True)
    owner = uuid.uuid4().hex
    network = "evdt-energy-test-" + owner
    truth = "evdt-energy-truth-" + owner
    report = {
        "passed": False,
        "scope": "Whole-mission partition design and local one-request worker isolation; not collected mission accuracy or live API authorization",
        "data_collected": False,
        "phase_2_complete": False,
        "cases": [],
    }
    extra = [
        Path(__file__),
        ROOT / "scripts/known_route_energy/run_energy_job.py",
        ROOT / "scripts/known_route_energy/qualify_energy_forecasts.py",
        ROOT / "src/evdt/energy/partitions.py",
        ROOT / "qualification/energy/mission-design.json",
        ROOT / "qualification/energy/forecast-cases.json",
    ]
    before = {
        **fingerprints(),
        **{
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in extra
        },
    }
    original_volumes = set(docker("volume", "ls", "-q").stdout.decode().splitlines())
    original_containers = set(docker("ps", "-aq").stdout.decode().splitlines())
    original_networks = set(docker("network", "ls", "-q").stdout.decode().splitlines())
    network_created = False
    try:
        design = parse_design(
            json.loads((ROOT / "qualification/energy/mission-design.json").read_bytes())
        )
        report["partition_design"] = {
            "design_sha256": design.design_sha256,
            "missions": len(design.missions),
            "counts": dict(Counter(m.partition for m in design.missions)),
            "status": "planned_not_collected",
            "whole_mission_ownership": True,
        }
        try:
            authorize_partition("held_out", "evaluate")
        except ValueError:
            report["held_out_gate_closed"] = True
        else:
            raise AssertionError("Held-out evaluation was not gated")
        image = build_image(output)
        report["image_id"] = image
        docker("network", "create", "--internal", "--label", "evdt.energy.owner=" + owner, network)
        network_created = True
        docker(
            "run",
            "--detach",
            "--name",
            truth,
            "--label",
            "evdt.energy.owner=" + owner,
            "--read-only",
            "--network",
            network,
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "64m",
            "--memory-swap",
            "64m",
            "--pids-limit",
            "16",
            "--tmpfs",
            "/private-truth:rw,noexec,nosuid,size=1m,uid=10001,gid=10001,mode=0700",
            "--entrypoint",
            "python",
            image,
            "-I",
            "-B",
            "-u",
            "-c",
            TRUTH_SERVER,
        )
        info = json.loads(docker("inspect", truth).stdout)[0]
        address = info["NetworkSettings"]["Networks"][network]["IPAddress"]
        positive = """
import json,pathlib,time,urllib.request
names=['actual_capacity','fault_labels','future_traffic','realization_seed']
for attempt in range(50):
    try:
        urllib.request.urlopen('http://127.0.0.1:8079/actual_capacity',timeout=.5).read()
        break
    except OSError:
        time.sleep(.1)
else:
    raise RuntimeError('Truth fixture did not start')
for name in names:
    expected=(pathlib.Path('/private-truth')/(name+'.json')).read_bytes()
    actual=urllib.request.urlopen('http://127.0.0.1:8079/'+name,timeout=1).read()
    assert expected==actual
print(json.dumps({'files_read':4,'endpoints_read':4,'passed':True}))
"""
        report["positive_truth_control"] = json.loads(
            docker("exec", truth, "python", "-I", "-B", "-c", positive).stdout
        )
        host_canary = output / "host-private-canary.json"
        host_canary.write_bytes(encode({"synthetic_host_private_canary": True}))
        host_path = linux_checkout() + "/" + host_canary.relative_to(ROOT).as_posix()
        probe = """
import hashlib,importlib.util,json,os,pathlib,socket,urllib.request
names=['actual_capacity','fault_labels','future_traffic','realization_seed']
paths=['/private-truth/'+n+'.json' for n in names]+[HOST_PATH,'/var/run/docker.sock','/proc/1/root/private-truth/actual_capacity.json']
for path in paths:
    try:
        pathlib.Path(path).read_bytes()
    except OSError:
        pass
    else:
        raise RuntimeError('Private file unexpectedly readable')
for name in names:
    try:
        urllib.request.urlopen('http://'+ADDRESS+':8079/'+name,timeout=.3).read()
    except OSError:
        pass
    else:
        raise RuntimeError('Private endpoint unexpectedly reachable')
for module in ['evdt.monitoring','evdt.energy.partitions','traci','psycopg']:
    assert importlib.util.find_spec(module) is None
assert [name for _,name in socket.if_nameindex()]==['lo']
status=pathlib.Path('/proc/self/status').read_text()
properties=dict(line.split(':',1) for line in status.splitlines() if ':' in line)
assert os.geteuid()==10001
assert properties['NoNewPrivs'].strip()=='1'
assert int(properties['CapEff'].strip(),16)==0
assert properties['Seccomp'].strip()=='2'
try:
    pathlib.Path('/tmp/unapproved-write').write_text('blocked')
except OSError:
    pass
else:
    raise RuntimeError('Worker root was writable')
base=pathlib.Path('/usr/local/lib/python3.12/site-packages')
hashes={name:hashlib.sha256((base/name.removeprefix('src/')).read_bytes()).hexdigest() for name in PACKAGED_PATHS}
print(json.dumps({'passed':True,'private_file_reads_blocked':len(paths),'private_endpoint_reads_blocked':len(names),'forbidden_imports_absent':4,'network_interfaces':['lo'],'uid':os.geteuid(),'effective_capabilities':0,'no_new_privileges':True,'seccomp_filter_active':True,'root_write_blocked':True,'packaged_source_sha256':hashes}))
"""
        probe = (
            "HOST_PATH="
            + repr(host_path)
            + "\nADDRESS="
            + repr(address)
            + "\nPACKAGED_PATHS="
            + repr([p.relative_to(ROOT).as_posix() for p in PACKAGED])
            + "\n"
            + probe
        )
        result = run_isolated(image, b"", command=["-I", "-B", "-c", probe])
        if result["exit_code"] or result["controller_exit_code"]:
            (output / "probe-error.txt").write_text(result["stderr"], encoding="utf-8")
            raise RuntimeError("Worker denial probe failed; inspect private probe error")
        report["access_denial"] = json.loads(result["stdout"])
        if report["access_denial"]["packaged_source_sha256"] != {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in PACKAGED
        }:
            raise AssertionError("Packaged source does not match reviewed bytes")

        data = json.loads((ROOT / "qualification/energy/forecast-cases.json").read_bytes())
        scope = ObservationScope(
            "simulation",
            "worker-run",
            "worker-bus",
            "worker-source",
            "worker-session",
            1,
            "simulation",
        )
        packets = synthetic_packets(data, scope)
        inputs = (
            packets,
            scope,
            ForecastClock(100, "recorded_analysis", "frozen"),
            MissionRequest(**data["mission"]),
            ForecastPolicy(**data["policy"]),
        )
        reference = make_job(*inputs)
        planned = make_job(
            *inputs,
            parameters=EnergyParameters(**data["parameters"]),
            plan=(DrivingInterval(100, 10, 10, 0, 36000),),
        )
        stale = json.loads(encode(reference))
        stale["clock"]["as_of_sim_time_s"] = 106
        future = json.loads(encode(reference))
        future["observations"][-1]["sim_time_s"] = 101
        for name, job in [
            ("reference", reference),
            ("planned", planned),
            ("stale_unavailable", stale),
            ("future_unavailable", future),
        ]:
            raw = encode(job)
            result = run_isolated(image, raw)
            expected = json.loads(encode(run_request(raw)))
            if (
                result["exit_code"] != 0
                or result["controller_exit_code"] != 0
                or json.loads(result["stdout"]) != expected
            ):
                raise AssertionError("Worker result mismatch: " + name)
            (output / (name + "-job.json")).write_bytes(raw)
            report["cases"].append(
                {
                    "id": name,
                    "passed": True,
                    "forecast_status": expected["forecast"]["status"],
                    "job_sha256": expected["job_sha256"],
                }
            )
        injected = {**reference, "hidden_future": [1, 2, 3]}
        rejected = run_isolated(image, encode(injected))
        if rejected["exit_code"] != 2 or json.loads(rejected["stdout"]) != {
            "version": "isolated-energy-result/1.0.0",
            "status": "rejected",
            "reason": "invalid_job",
        }:
            raise AssertionError("Hidden-field job was not rejected cleanly")
        report["cases"].append({"id": "hidden_field_rejected", "passed": True})
        report["passed"] = len(report["cases"]) == 5
    except Exception as exc:
        report["error"] = str(exc) or type(exc).__name__
    finally:
        try:
            remove_owned(truth, owner)
            if network_created:
                net = json.loads(docker("network", "inspect", network).stdout)[0]
                if net.get("Labels", {}).get("evdt.energy.owner") != owner or net["Containers"]:
                    raise RuntimeError("Refuse cleanup of unowned or occupied network")
                docker("network", "rm", network)
            after_volumes = set(docker("volume", "ls", "-q").stdout.decode().splitlines())
            after_containers = set(docker("ps", "-aq").stdout.decode().splitlines())
            after_networks = set(docker("network", "ls", "-q").stdout.decode().splitlines())
            report["cleanup"] = {
                "passed": original_volumes == after_volumes
                and original_containers == after_containers
                and original_networks == after_networks,
                "preexisting_volumes_preserved": len(original_volumes & after_volumes),
                "new_volumes": len(after_volumes - original_volumes),
                "new_containers": len(after_containers - original_containers),
                "missing_preexisting_volumes": len(original_volumes - after_volumes),
                "owned_network_removed": True,
                "preexisting_networks_preserved": original_networks == after_networks,
            }
            report["passed"] = report["passed"] and report["cleanup"]["passed"]
        except Exception as exc:
            report.update(passed=False, cleanup_error=str(exc))
    report["source_sha256"] = before
    if any(
        not (ROOT / p).is_file() or hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h
        for p, h in before.items()
    ):
        report.update(passed=False, error="Source inputs changed during qualification")
    (output / "qualification.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Energy boundary passed: {report['passed']}; cases: {len(report['cases'])}; report: {output.relative_to(ROOT)}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
