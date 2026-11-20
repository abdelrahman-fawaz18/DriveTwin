# Verify the connected speed-limit command

The operator sends a request to the command service over HTTP, the protocol used to make requests to a local web service. PostgreSQL keeps the request and result on disk. Restarting the service can interrupt a request, so the service saves a reservation before it gives the command to the simulator.

| Record | What it tells you |
|---|---|
| Request | The operator was authorized and the command is queued for this simulation run and bus. |
| Reservation | The command was handed out or may have been handed out. Without a result, the outcome is uncertain and a second handoff is refused. |
| Execution result | The simulator acknowledged the call or the executor could not establish its outcome. |
| Speed measurements | What the simulated bus actually did on the route. The qualification compares these with a baseline run. |

The operator credential can submit and look up commands. The simulator credential can reserve commands and report results. An existing monitoring reader can still read the monitoring catalog and is denied command access. The service uses a separate database login restricted to its command tables.

1. Open PowerShell in `<checkout>`. The check uses the previously built `evdt-energy-collector:p2` image, a packaged copy of the pinned simulator and Python tools. It runs alongside the existing monitoring service in a temporary Docker Compose project. Compose starts and stops a group of containers as one named project.
2. Run the check with a new evidence folder. `--timeout 245` limits the complete Ubuntu run, including cleanup. The qualification creates fresh local credentials, selects unused local ports, and submits one 4 m/s command through the operator endpoint.

   ```powershell
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --evidence-dir evidence/p3/my-connected-command-check
   ```

3. Expect progress for credential checks, simulator delivery, and restart verification. The check restarts its own PostgreSQL database and replaces its command service. Success prints `Connected speed limit check passed: True`. The evidence folder contains `qualification.json` and `drive/qualification.json`, with separate records of the acknowledged execution and observed speed. The controller removes its temporary containers, network and database volume at the end. Use a new evidence folder for each run.

For this profile, the operator sends a versioned request to `POST /v1/commands` and retrieves the state with `GET /v1/commands/<command-id>`. `201` means a new request was saved. `200` on an exact retry returns its existing state. `401` means the credential is missing or unsuitable for the route. `403` means the issuer or target falls outside the configured scope. `409` means a request or result conflicts with the journal. `422` means the command failed validation. The local simulator asks for the command at simulated second 4. The request must be valid at that instant and still within its real-time deadline. The command cap stays in effect for the bounded drive.

To use the optional Compose services in a prepared local deployment, supply a registered simulation run, `COMMAND_ASSET_ID`, `COMMAND_ISSUER`, a private `COMMAND_EVIDENCE_DIR`, and separate `command_db`, `command_operator`, `command_executor`, and `command_observer` files in the deployment's secret directory. Start `command-api` with the Phase 1 and Phase 3 Compose files together. The simulator is an explicit one-off `command-simulator` service. Use a fresh run for another controlled drive. The qualification command above provisions these inputs. [Observation and recovery guide](command-recovery-check.md)

The full stack for this check is disposable, including its database volume after evidence has been collected. The restart test keeps the database volume while the command service and database restart. The preserved local evidence contains the test results after cleanup. [Engineering decision](../adr/0047-authenticated-command-journal.md) · [Command format](../../contracts/commands/speed-limit-1.0.0.schema.json)

The reviewed run recorded one request, one reservation and one result. The result matched exactly after the restart. The baseline reached 8.33 m/s and the commanded bus reached 4.00 m/s. The command role also failed attempts to read raw telemetry, delete the request and update the result. [Verification record](../verification/connected-speed-limit.json)
