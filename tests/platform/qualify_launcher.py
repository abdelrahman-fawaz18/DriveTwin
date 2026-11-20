"""Qualify the actual Windows-to-WSL launcher with real Linux processes.

Run from Windows: python tests/platform/qualify_launcher.py --report evidence/p0/REPORT.json
All fixture files stay in a unique .runtime/qualification directory for inspection.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib
import threading
import traceback
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts/linux.py"
OBSERVATIONS = []
SCRATCH = ROOT / ".runtime" / "qualification" / uuid.uuid4().hex


def invoke(command, *, options=(), data=None, cwd=ROOT, timeout=40):
    args = [sys.executable, str(LAUNCHER), *options, "--", *command]
    result = subprocess.run(args, input=data, capture_output=True, cwd=cwd, timeout=timeout)
    OBSERVATIONS.append({
        "command": args, "exit_code": result.returncode,
        "stdout": result.stdout.decode("utf-8", errors="backslashreplace"),
        "stderr": result.stderr.decode("utf-8", errors="backslashreplace"),
    })
    return result


def wait_for(path, process=None, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        if process is not None and process.poll() is not None:
            stdout, stderr = process.communicate(timeout=3)
            raise AssertionError(f"Command ended before readiness: {stdout!r} {stderr!r}")
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {path}")


def tree_command(pid_file):
    # Both programs ignore graceful termination, exercising the forced stop path.
    child = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "Path(__import__('sys').argv[1]).write_text(str(os.getpid())); time.sleep(120)"
    )
    parent = (
        "import json,os,signal,subprocess,sys,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        "flag=Path(sys.argv[1]+'.child'); "
        "p=subprocess.Popen([sys.executable,'-c',sys.argv[2],str(flag)]); "
        "deadline=time.monotonic()+10\n"
        "while not flag.exists() and time.monotonic()<deadline: time.sleep(.05)\n"
        "assert flag.exists(), 'child failed to start'\n"
        "Path(sys.argv[1]).write_text(json.dumps({'pids':[os.getpid(),p.pid]})); time.sleep(120)"
    )
    return ["python3", "-c", parent, pid_file.relative_to(ROOT).as_posix(), child]


def start_tree(*options):
    run_id = uuid.uuid4().hex
    pid_file = SCRATCH / f"{run_id}-pids.json"
    args = [sys.executable, str(LAUNCHER), "--run-id", run_id, *options, "--", *tree_command(pid_file)]
    process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        pids = wait_for(pid_file, process)["pids"]
    except BaseException:
        subprocess.run([sys.executable, str(LAUNCHER), "--cancel", run_id], cwd=ROOT, timeout=5)
        process.communicate(timeout=25)
        raise
    return process, run_id, pids


def finished_state(run_id, timeout=25):
    path = ROOT / ".runtime/launcher" / run_id / "state.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = wait_for(path, timeout=2)
        if result["state"] == "finished":
            OBSERVATIONS.append({"completion": result})
            return result
        time.sleep(.1)
    raise AssertionError("Linux worker did not report completion")


class LauncherQualification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SCRATCH.mkdir(parents=True)
        spec = importlib.util.spec_from_file_location("qualified_linux_launcher", LAUNCHER)
        cls.launcher_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.launcher_module)

    def test_temporary_windows_reader_lock_allows_eventual_atomic_write(self):
        target = SCRATCH / "temporary-lock.json"
        target.write_text('{"old": true}')
        replacement = SCRATCH / "lock-reproduction.json"
        replacement.write_text('{"new": true}')
        with target.open("rb"):
            with self.assertRaises(PermissionError):
                os.replace(replacement, target)
        held = target.open("rb")

        def release():
            time.sleep(.2)
            held.close()

        releaser = threading.Thread(target=release)
        releaser.start()
        try:
            self.launcher_module.write_json(target, {"new": True})
        finally:
            releaser.join(timeout=3)
            held.close()
        self.assertEqual(json.loads(target.read_text()), {"new": True})
        self.assertEqual(list(SCRATCH.glob("temporary-lock.json.*.tmp")), [])

    def test_persistent_windows_lock_fails_and_preserves_existing_data(self):
        target = SCRATCH / "persistent-lock.json"
        target.write_text('{"old": true}')
        started = time.monotonic()
        with target.open("rb"):
            with self.assertRaises(PermissionError):
                self.launcher_module.write_json(target, {"new": True})
        self.assertLess(time.monotonic()-started, 3)
        self.assertEqual(json.loads(target.read_text()), {"old": True})
        self.assertEqual(list(SCRATCH.glob("persistent-lock.json.*.tmp")), [])

    def assert_no_live_processes(self, pids):
        probe = (
            "import json,sys; from pathlib import Path; live=[]\n"
            "for pid in json.loads(sys.argv[1]):\n"
            " p=Path('/proc')/str(pid)/'stat'\n"
            " try: state=p.read_text().rpartition(')')[2].split()[0]\n"
            " except FileNotFoundError: continue\n"
            " if state not in ('Z','X'): live.append(pid)\n"
            "print(json.dumps(live))"
        )
        result = invoke(["python3", "-c", probe, json.dumps(pids)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [], result.stdout)

    def test_arguments_are_literal_and_exact(self):
        marker = SCRATCH / "must-not-be-created"
        values = ["", "two words", "quote\"inside", "'single'", "Montréal ☃", "line\nbreak",
                  "trailing\\", r"C:\folder with spaces\file", "--a-flag", "| & < > $ % !",
                  f"$(touch {marker.relative_to(ROOT).as_posix()})", "`touch must-not-exist`"]
        result = invoke(["python3", "-c", "import json,sys; print(json.dumps(sys.argv[1:]))", *values])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), values)
        self.assertFalse(marker.exists())
        self.assertFalse((ROOT / "must-not-exist").exists())

    def test_binary_stdin_stdout_and_separate_stderr(self):
        data = b"bus-01\x00\xff\r\n" + "Montréal".encode()
        result = invoke(["python3", "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.buffer.write(b'error-stream\\n')"], data=data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, data)
        self.assertEqual(result.stderr, b"error-stream\n")

    def test_configuration_environment_is_scoped_to_linux_command(self):
        expected = tomllib.loads((ROOT / "environments/dev.toml").read_text())["linux"]["environment"]
        before = {key: os.environ.get(key) for key in expected}
        result = invoke(["python3", "-c", "import json,os,sys; print(json.dumps({k:os.environ.get(k) for k in json.loads(sys.argv[1])}))", json.dumps(list(expected))])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual({key: os.environ.get(key) for key in expected}, before)

    def test_root_is_independent_of_windows_calling_directory(self):
        result = invoke(["python3", "-c", "import os; print(os.getcwd())"], cwd=SCRATCH)
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = tomllib.loads((ROOT / "environments/dev.toml").read_text())["linux"]
        mount = settings["windows_mount_root"].rstrip("/")
        expected = f"{mount}/{ROOT.drive[0].lower()}/{ROOT.relative_to(ROOT.anchor).as_posix()}"
        self.assertEqual(result.stdout.decode().strip(), expected)

    def test_working_directory_with_spaces_and_unicode(self):
        folder = SCRATCH / "Montréal bus history"
        folder.mkdir()
        result = invoke(["python3", "-c", "import os; print(os.path.basename(os.getcwd()))"],
                        options=["--cwd", folder.relative_to(ROOT).as_posix()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.decode().strip(), folder.name)

    def test_outside_project_directory_is_rejected(self):
        result = invoke(["python3", "--version"], options=["--cwd", ".."])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"inside the EV-DT project", result.stderr)

    def test_failed_command_returns_its_exit_code(self):
        result = invoke(["python3", "-c", "raise SystemExit(7)"])
        self.assertEqual(result.returncode, 7, result.stderr)

    def test_missing_executable_is_an_error(self):
        result = invoke(["ev-dt-program-that-does-not-exist"])
        self.assertEqual(result.returncode, 127, result.stderr)

    def test_signal_exit_is_reported(self):
        result = invoke(["python3", "-c", "import os,signal; os.kill(os.getpid(),signal.SIGTERM)"])
        self.assertEqual(result.returncode, 143, result.stderr)

    def test_linux_file_and_receipt_are_readable_from_windows(self):
        artifact = SCRATCH / "battery reading.json"
        receipt = SCRATCH / "artifact receipt.json"
        data = {"asset": "bus-01", "voltage_v": 602.5}
        result = invoke(["python3", "-c", "import json,sys; from pathlib import Path; Path(sys.argv[1]).write_text(json.dumps(json.loads(sys.argv[2])))", artifact.relative_to(ROOT).as_posix(), json.dumps(data)],
                        options=["--receipt", receipt.relative_to(ROOT).as_posix()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(artifact.read_text()), data)
        metadata = json.loads(receipt.read_text())
        self.assertEqual(metadata["distribution"], "Ubuntu-24.04")
        self.assertNotEqual(metadata["uid"], 0)
        self.assertTrue(metadata["cleanup_confirmed"])

    def test_real_linux_unittest_suite(self):
        result = invoke(["python3", "-m", "unittest", "discover", "-s", "tests/platform", "-p", "test_linux_environment.py", "-v"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"Ran 3 tests", result.stderr)

    def test_timeout_stops_term_resistant_process_tree(self):
        process, run_id, pids = start_tree("--timeout", "6")
        stdout, stderr = process.communicate(timeout=20)
        self.assertEqual(process.returncode, 124, (stdout, stderr))
        state = finished_state(run_id)
        self.assertEqual(state["reason"], "timed_out")
        self.assertTrue(state["cleanup_confirmed"])
        self.assert_no_live_processes(pids)

    def test_cancellation_preserves_another_concurrent_run(self):
        process, run_id, pids = start_tree()
        other_id = uuid.uuid4().hex
        other = subprocess.Popen([sys.executable, str(LAUNCHER), "--run-id", other_id, "--", "python3", "-c", "import time; time.sleep(4); print('other-run-completed')"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            cancel = subprocess.run([sys.executable, str(LAUNCHER), "--cancel", run_id], cwd=ROOT, capture_output=True, timeout=5)
            self.assertEqual(cancel.returncode, 0, cancel.stderr)
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 130, (stdout, stderr))
            state = finished_state(run_id)
            self.assertTrue(state["cleanup_confirmed"])
            self.assertEqual(state["reason"], "cancelled")
            stdout, stderr = other.communicate(timeout=15)
            self.assertEqual(other.returncode, 0, stderr)
            self.assertEqual(stdout, b"other-run-completed\n")
            self.assert_no_live_processes(pids)
        finally:
            if process.poll() is None:
                subprocess.run([sys.executable, str(LAUNCHER), "--cancel", run_id], cwd=ROOT, timeout=5)
                process.communicate(timeout=25)
            if other.poll() is None:
                subprocess.run([sys.executable, str(LAUNCHER), "--cancel", other_id], cwd=ROOT, timeout=5)
                other.communicate(timeout=25)

    def test_forced_windows_controller_loss_stops_linux_tree(self):
        process, run_id, pids = start_tree()
        process.kill()
        process.communicate(timeout=30)
        state = finished_state(run_id)
        self.assertEqual(state["reason"], "parent_lost")
        self.assertTrue(state["cleanup_confirmed"])
        self.assert_no_live_processes(pids)

    def test_forced_windows_process_tree_loss_stops_linux_tree(self):
        process, run_id, pids = start_tree()
        # The captured PID is a live test-created controller, not a process name.
        killed = subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, timeout=10,
        )
        self.assertEqual(killed.returncode, 0, killed.stderr)
        process.communicate(timeout=30)
        state = finished_state(run_id)
        self.assertIn(state["reason"], {"parent_lost", "cancelled"})
        self.assertTrue(state["cleanup_confirmed"])
        self.assert_no_live_processes(pids)

    def test_normal_completion_cleans_up_background_descendant(self):
        child_pid = SCRATCH / "background-child.json"
        child = (
            "import json,os,signal,sys,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "Path(sys.argv[1]).write_text(json.dumps({'pids':[os.getpid()]})); time.sleep(120)"
        )
        parent = (
            "import subprocess,sys,time; from pathlib import Path; "
            "subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]]); "
            "deadline=time.monotonic()+10\n"
            "while not Path(sys.argv[1]).exists() and time.monotonic()<deadline: time.sleep(.05)\n"
            "assert Path(sys.argv[1]).exists()"
        )
        result = invoke(["python3", "-c", parent, child_pid.relative_to(ROOT).as_posix(), child])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_live_processes(wait_for(child_pid)["pids"])


class RecordingResult(unittest.TextTestResult):
    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.records.append({"test": test.id(), "passed": True, "seconds": time.monotonic() - self.started})

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.records.append({"test": test.id(), "passed": False, "error": "".join(traceback.format_exception(*error))})

    def addError(self, test, error):
        super().addError(test, error)
        self.records.append({"test": test.id(), "passed": False, "error": "".join(traceback.format_exception(*error))})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    output = args.report.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("Save the qualification report inside the project.")
    if os.name != "nt":
        parser.error("This qualification specifically requires Windows Python invoking WSL.")
    RecordingResult.records = []
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LauncherQualification)
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Windows-native agent + real Ubuntu command-launcher integration",
        "host_platform": sys.platform, "python": sys.version,
        "tests_run": result.testsRun, "passed": result.wasSuccessful(),
        "tests": result.records, "observations": OBSERVATIONS,
        "fixture_directory": str(SCRATCH),
        "limits": ["Foreground commands and descendants staying in their Linux process group",
                   "Console Ctrl+C and the app UI Stop button are not directly exercised by this automated suite",
                   "No global agent settings, other-project chats, containers, or resource limits changed"],
    }, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
