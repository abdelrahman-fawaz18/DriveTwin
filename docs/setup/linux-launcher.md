# Windows-to-WSL command launcher

The launcher runs Ubuntu commands from Windows PowerShell and propagates output, exit status, and cancellation. [Qualification record](../foundation-status.md)

## Configuration

The entry point is [scripts/linux.py](../../scripts/linux.py). Its settings live in `environments/dev.toml`, generated from the [settings template](../../environments/dev.example.toml).

## Command syntax

From PowerShell in the repository root:

```powershell
python scripts/linux.py -- python3 --version
```

`--` separates launcher options from the Ubuntu command.

The same entry point can run directly inside Linux with `python3 scripts/linux.py -- ...`; the Windows-to-WSL path is the one qualified by the integration suite.

## Working folders and generated files

The default working folder is the project root, regardless of the Windows shell's current folder. To select a subfolder, pass a path relative to the project:

```powershell
python scripts/linux.py --cwd tests/platform -- python3 -m unittest test_linux_environment -v
```

`--cwd` means the folder in which the command starts. Spaces and non-English characters in folder names were tested. A requested working folder outside the project is rejected. Commands themselves still have the permissions provided by the approved execution environment; the launcher is not a security sandbox.

Generated project files are the same files Windows can read. Programs should use project-relative or Linux paths inside Ubuntu. The launcher preserves arguments literally; it does not automatically rewrite an argument containing a Windows path.

## Limits, cancellation, and completion records

```powershell
python scripts/linux.py --timeout 30 --receipt evidence/p0/example-command.json -- python3 --version
```

`--timeout 30` requests a 30-second deadline, including the bridge startup wait. Shutdown and operating-system process creation can add latency; this is a development convenience, not a real-time deadline guarantee.

`--receipt` saves a small JSON completion record in the project. JSON means JavaScript Object Notation: a text format that stores named values and lists. This receipt records the Linux account, folder, command outcome, and cleanup result. It does not capture the command's output; output continues through the normal output/error channels.

A **process** is a running instance of a program. A command can start additional processes. The launcher places these in an owned Linux **process group**, so cancellation targets that command and its related processes. Other concurrent launcher commands have separate groups.

There are three tested ways to stop work:

1. **Ctrl+C** in a terminal requests cancellation. A real terminal interruption was exercised in addition to the automated suite.
2. **Timeout** requests cancellation after the chosen duration.
3. **Explicit cancellation** targets a particular run ID. This is useful for automated tasks:

   ```powershell
   python scripts/linux.py --run-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa -- python3 -c "import time; time.sleep(120)"
   ```

   From another terminal or automation call:

   ```powershell
   python scripts/linux.py --cancel aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
   ```

   Each new invocation needs a unique 32-character lowercase hexadecimal run ID. Reusing an existing ID is rejected. Normally the launcher generates one automatically.

The launcher requests graceful termination first, then forcefully stops remaining processes after the configured two-second grace period. A **heartbeat** is a small “still here” update written once per second. If the Windows controller disappears and updates stop, the Linux worker detects the unchanged heartbeat after 15 seconds and stops its command group. This uses elapsed time, so it does not depend on Windows and Linux wall clocks matching. Tests forcibly stopped the Windows controller alone and its Windows process tree, then independently checked the recorded Linux process IDs were no longer running.

On normal command completion, remaining processes in its group are also cleaned up. This launcher manages foreground development commands. Programs that deliberately detach into a separate session/process group need their own explicit lifecycle management. Later service containers will have that separate lifecycle.

## Common results

| Exit code | Meaning |
|---|---|
| 0 | Command succeeded |
| Other ordinary command code, such as 7 | The Linux program's own failure result |
| 124 | Requested timeout |
| 125 | Launcher/setup/transport error or cleanup not confirmed; inspect the error and record |
| 126 | Linux execution error, such as a permission failure |
| 127 | Executable not found |
| 129 | Controller heartbeat lost |
| 130 | Cancellation requested |
| 128 plus a signal number | A Linux command ended from that operating-system signal; for example, 143 for SIGTERM |

A **signal** is an operating-system notification to a process. SIGTERM requests termination; SIGKILL forces termination. The worker uses them only for its owned command group. The receipt's reason distinguishes launcher cancellation from a command that ended from a signal independently.

In the real Ctrl+C test, the outer PowerShell terminal reported exit 1 because it was interrupted; the Linux receipt recorded cancellation with exit 130 and confirmed cleanup. Record the shell interruption and Linux cancellation as separate outcomes.

## How it works internally

1. Windows reads the configuration and creates a unique request directory under .runtime/launcher.
2. It writes the command as a JSON list of arguments. It launches the fixed Linux worker through `wsl.exe --exec`. The command is not assembled into a PowerShell or Bash string.
3. The Linux worker reads the request and starts the program with separate arguments. This preserves empty arguments, quotes, Unicode, and shell-looking characters as data.
4. Input, normal output, and error output pass through the process connection. Binary input/output was tested; the launcher does not use the input channel to send its request.
5. The worker monitors completion, cancellation, and heartbeat updates, cleans up its command group, and writes its result.
6. Windows checks that the bridge exit code matches the Linux result. A missing or inconsistent completion record is an error, not success.

Temporary requests, heartbeat files, test fixtures, and worker records live under .runtime, which is excluded from future Git tracking. Permanent acceptance evidence lives under evidence/p0. The launcher uses Python's standard library and requires Python 3.11 or later; the tested versions are Windows Python 3.13.7 and Ubuntu Python 3.12.3. No additional Python library installation was required.

### Project-specific program settings and shared-file behavior

The optional linux.environment table in environments/dev.toml holds string name/value pairs passed only to Linux child programs. An **environment variable** is a named setting a program can read. DOCKER_HOST selects the local Ubuntu engine endpoint; DOCKER_CONFIG selects a separate project client-settings folder. Neither changes the Windows environment or Windows/Desktop credentials. The qualification suite checks this isolation. See the [Docker setup guide](docker-engine.md).

A longer real run exposed a temporary Windows refusal to replace an open heartbeat file. The Linux worker's missing-heartbeat fallback stopped the affected command correctly. The failure record is preserved. The helper now retries a PermissionError during file replacement for at most one second. A persistent failure is still reported and the prior data is retained.

The file is replaced **atomically**, meaning a reader sees a complete previous or next JSON record. New tests reproduce a real Windows reader lock, verify recovery after it closes, and verify bounded failure with unchanged old data if it stays open. This is tested file-operation handling, not an operating-system permission or security-setting change.

## Reproduce the qualification

From Windows PowerShell in the project folder:

```powershell
python tests/platform/qualify_launcher.py --report evidence/p0/launcher-qualification-repeat.json
```

This starts real test-owned Linux programs. It tests literal arguments, binary input/output, separate error output, working folders, failure results, file exchange, three native Linux tests, timeouts, concurrent cancellation, controller loss, forced Windows process-tree loss, and background-child cleanup. Use a new report filename to preserve the original evidence.

The launcher uses the configured Ubuntu account and its existing permissions.

The automated suite does not directly click the desktop app's Stop button. It exercises explicit cancellation and abrupt controller/process-tree termination; the separate terminal test exercises actual Ctrl+C. Host suspension, deliberate process-group escape, and a crash of the Linux supervisor itself are outside the current qualification scope.

## Evidence and references

- [Current P0-01 acceptance record](../verification/README.md)
- [18-test qualification report](../verification/README.md)
- [Observed file-lock failure and fallback cleanup](../verification/README.md)
- [Final-source actual terminal interruption](../verification/README.md)
- [Initial P0-01 acceptance record](../verification/README.md)
- [Earlier 15-test qualification report](../verification/README.md)
- [Initial 13-test report](../verification/README.md)
- [Actual terminal interruption](../verification/README.md)
- [Windows/Linux file-edit round trip](../verification/README.md)
- [Test program](../../tests/platform/qualify_launcher.py) and [Linux test suite](../../tests/platform/test_linux_environment.py)
- [Microsoft WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)
- [Python subprocess reference](https://docs.python.org/3.13/library/subprocess.html) and [Linux process-group signaling](https://docs.python.org/3.12/library/os.html#os.killpg)
