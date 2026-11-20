# Check speed-cap failures in the local simulator

An actuator carries out a command. In this simulation, the speed-limit interface asks SUMO to change the bus's maximum speed. An acknowledgment tells us the call returned normally. The speed observations tell us whether the bus actually slowed down.

Both tests submit a 4 m/s cap, reserve it once, and inject a fault at the simulator's actuator call. The observer then reads the normal simulated speed samples from both drives and sends them to the command service. The service computes the effect and stores it separately from the execution result. A missing sample leaves the effect empty until complete evidence arrives.

| Case | Injected behavior | Execution result | Measured effect |
|---|---|---|---|
| `stuck` | The actuator call returns normally and leaves the existing cap in place. | `acknowledged` | `not_observed` |
| `failed` | The actuator call raises a timeout error and leaves the existing cap in place. | `unknown_outcome` | `not_observed` |

1. Open PowerShell in `<checkout>`. Use the previously built local simulator image. [Connected command setup](connected-speed-limit-command.md)
2. Run one case at a time. `--actuator-fault` selects the injected behavior. `--evidence-dir` names a new private results folder. `--timeout 245` bounds each check including cleanup.

   ```powershell
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --actuator-fault stuck --evidence-dir evidence/p3/my-actuator-stuck
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --actuator-fault failed --evidence-dir evidence/p3/my-actuator-failed
   ```

3. Expect admission checks, a simulator drive, restart and observation checks, then `Connected speed limit check passed: True`. Open each `qualification.json` and check the execution result against the table. Confirm that `actuator_calls` is `1`, `identical_speed_traces` is `true`, and `independent_speed_effect.confirmed` is `false`. The runner also verifies that both records survive a service replacement and database restart, that a second handoff is refused, and that the temporary resources are removed.

The fault is injected at the adapter boundary while SUMO continues driving. This exercise covers a single local command and its recorded effect. The subsequent [live command check](live-speed-command-check.md) connects the command path to regular telemetry and verifies the speed effect. [Effect rules](../adr/0048-command-reconciliation-from-speed.md)
