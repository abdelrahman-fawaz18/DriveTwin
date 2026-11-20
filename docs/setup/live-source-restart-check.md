# Verify restart safety for the live simulator

The command service saves a reservation before it hands out the speed cap. Think of it as a durable record that the bus may already have acted. If the simulator disappears, a retry returns the saved state and the service refuses a second handoff.

The qualification starts the normal monitoring source with a test-only stop point. It kills the disposable source container, checks the stored command and readings, then tries to restart the same run. Finally it provisions a fresh run in the same PostgreSQL database with a separate queue and new credentials.

| Case | Expected result |
|---|---|
| `before_actuation` | The service records one reservation and four accepted speed samples from before the command could take effect. |
| `after_actuation` | The simulator applies 4 m/s and the monitoring service accepts eight samples, including capped readings. The test drops the execution reply. |

Both cases should retain `unknown_outcome`. The partial trace stays available in monitoring. The command journal requires the full comparison window before it can confirm a measured effect.

1. Open PowerShell in `<checkout>`. Use the same pinned simulator image as the live speed command check. [Live command setup](live-speed-command-check.md)
2. Run the two cases one at a time. `--live-restart` selects the stop point. Each `--evidence-dir` must be new. The 245-second limit covers startup, the interruption and fresh drive, and cleanup.

   ```powershell
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --live-restart before_actuation --evidence-dir evidence/p3/my-restart-before
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --live-restart after_actuation --evidence-dir evidence/p3/my-restart-after
   ```

3. Expect a message when the test kills its source, tries the same run, and starts the fresh one. Success prints `Connected speed limit check passed: True`. In `qualification.json`, check for exit code `137` from the injected kill, `same_run_restart_refused`, `queue_state_preserved`, and `old_attempts` equal to `1`. The fresh run should complete 16 observations, keep the old command in its original run, and have an empty command journal of its own. `cleanup_passed` confirms the temporary resources were removed.

The real application refuses a same-run start before changing the queue. For routine recovery in this profile, keep the interrupted queue and evidence and provision an explicit fresh run. The test performs that workflow for its temporary resources. [Restart decision](../adr/0052-refuse-unsafe-simulator-restarts.md)
