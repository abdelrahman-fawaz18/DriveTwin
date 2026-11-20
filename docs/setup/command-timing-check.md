# Check command expiry and rejection records

A speed cap can be valid during a specific part of the drive and also have a real-time deadline. Both conditions must hold when the bus is ready to apply it. For example, the simulator can pause at second 4 while a 12-second real-time deadline continues counting down.

| Case | Expected behavior |
|---|---|
| Pause past the real-time deadline | The simulator stays at simulated second 4 during the pause. The service refuses handoff while paused and when it resumes after expiry. |
| Reach the simulation deadline first | The simulator advances to second 4 while the real-time deadline is still ahead. The simulation window closes, and retrying with an earlier time cannot reopen it. |
| Expire after handoff | The service reserves the command while valid. The simulator waits before applying it, detects the elapsed deadline, and records a refusal before actuation. |

The command service also audits attempts with unsuitable credentials, the wrong run or bus, a replay command, an unsupported capability, and an expired request. An audit record contains a reason code and trusted context. The request and credential stay outside that record. The clock-skew checks inject readings into the test process. They exercise the coordinator’s actual audit path and the clock guard’s rejection of a changed boot identity.

1. Open PowerShell in `<checkout>`. Use the previously built simulator image and the project’s Ubuntu launcher.
2. Run these checks one at a time. Each `--timing` option selects a scenario, and each evidence directory must be new. `--timeout 245` gives each complete check a time limit, including cleanup.

   ```powershell
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --timing pause_expiry --evidence-dir evidence/p3/my-timing-pause
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --timing accelerated_expiry --evidence-dir evidence/p3/my-timing-accelerated
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --timing delivery_expiry --evidence-dir evidence/p3/my-timing-delivery
   ```

3. Expect `Connected speed limit check passed: True` for each case. In `qualification.json`, check `matching_speed_traces`, `no_actuation`, `restart_and_retry_refused_handoff`, and the audit reason counts. The first two cases should finish as `expired`. The last should finish as `rejected_before_actuation` with an `elapsed_deadline` reason. The simulator traces and service logs remain in the private evidence directory.

When upgrading the optional local command services, apply migration 003 and use the matching simulator version. The internal claim request includes `state` as well as `sim_time_s`. A claim response carries its clock lease in a header while its body retains the original command bytes. [Timing decision](../adr/0049-command-deadlines-and-rejection-audit.md)

The reviewed pause lasted 12.14 real seconds at simulated second 4. The accelerated case rejected an earlier timestamp even before the real-time deadline. In the delayed handoff case the executor recorded `elapsed_deadline`, and a later attempt to report acknowledgment conflicted with that refusal. All three speed traces matched their baselines. [Verification record](../verification/command-timing.json)

The upgrade check initialized the previous journal from commit `a43dbbc` and queued a valid sample command. After migration 003, the command kept its original identity and the service recorded `missing_clock_lease` before the original wall deadline elapsed. It created no reservation.
