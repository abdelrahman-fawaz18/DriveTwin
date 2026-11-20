# Recover a speed command after an interruption

The simulator can slow down even if its reply never reaches the command service. Reconciliation compares the speed measurements with a run of the same route without the cap. It keeps the observed effect alongside the execution result so you can see what the system knows about each.

| Interruption | What the check does |
|---|---|
| Before actuation | Saves the reservation, then terminates the transport process before it can give the command to the simulator. |
| After actuation | Lets the simulator apply the cap, then terminates the transport process before it can publish the result. |
| Before result acknowledgment | Saves the result in PostgreSQL, then terminates the transport process before it can return that acknowledgment to the caller. |

Each case keeps the simulator alive to finish its speed trace. The test replaces the command service and restarts its PostgreSQL container, using the same database volume. It retries the command and verifies that the earlier reservation still prevents a second handoff. The separate observer submits speed measurements after the restart. The command service computes the effect from those measurements and preserves the original execution state.

1. Open PowerShell in `<checkout>`. The commands use the existing pinned simulator image and run one disposable Docker Compose project at a time.
2. Run the three cases below. Each `--fault` value selects one interruption point. Each `--evidence-dir` must be a new folder. The 245-second limit is for the whole check, including service startup and cleanup. Normal runs take much less time.

   ```powershell
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --fault before_actuation --evidence-dir evidence/p3/my-recovery-before
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --fault after_actuation --evidence-dir evidence/p3/my-recovery-after
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --fault before_result_ack --evidence-dir evidence/p3/my-recovery-ack
   ```

3. Each case should print `Connected speed limit check passed: True`. Open its `qualification.json` and compare `final_execution_state` with `independent_speed_effect.status`. The first case should read `unknown_outcome` and `not_observed`. The second should read `unknown_outcome` and `confirmed`. The third should read `acknowledged` and `confirmed`. The `drive` folder holds the actual speed observations and the transport interruption record. The qualification removes its own temporary resources after writing the report.

For an existing local command deployment, add a distinct `command_observer` secret before deploying the updated command services. The command initializer applies migration `002_speed_evidence.sql` after checking the original migration fingerprint. The observer receives read-only access to the recorded traces. Restart and qualify the updated optional services before using the new observation endpoint. [Engineering decision](../adr/0048-command-reconciliation-from-speed.md) · [Evidence format](../../contracts/commands/speed-evidence-1.0.0.schema.json)

All three reviewed interruptions passed. In the first case both speed traces peaked at 8.33 m/s during the comparison window. In the other two cases the commanded run peaked at 4.00 m/s against the same 8.33 m/s baseline. The observed effect survived a second database and command service restart in the lost-result case. [Verification record](../verification/command-recovery.json)

A separate compatibility check used the P3-A2 journal from commit `c5f6692` to initialize a database and save a sample result. After migration 002, the current service returned the original result with an empty effect field and refused a second handoff. This check exercised stored records without running another drive.
