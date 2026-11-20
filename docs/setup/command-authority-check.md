# Check competing speed caps in the local simulator

The command service chooses who gets to request a speed cap. The bus checks whether it can safely apply that cap. For example, a winning request for 4 meters per second (m/s) is refused if the bus already has a local ceiling of 3 m/s.

Docker Compose starts the temporary database, command service, and simulator from project configuration. A Compose overlay adds the arbitration settings to this test session. SUMO runs the bus drive twice on the same route, once as a reference and once with the selected command. Comparing the two speed traces shows what changed.

1. Open PowerShell in `<checkout>`. The commands use the project's Ubuntu launcher, native Docker Engine, and previously built simulator image. See the [connected command setup](connected-speed-limit-command.md) for the image prerequisite.
2. Run one case at a time. `--authority` selects the scenario. `--evidence-dir` names a new private results folder. `--timeout 245` bounds each run, including cleanup.

   ```powershell
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --authority operator_wins --evidence-dir evidence/p3/my-authority-operator
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --authority thermal_fallback --evidence-dir evidence/p3/my-authority-fallback
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --authority stale_fence --evidence-dir evidence/p3/my-authority-stale
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --authority local_protection --evidence-dir evidence/p3/my-authority-protection
   ```

3. Expect three proposals, two competing seal requests, a simulator drive, a service replacement and database restart, then `Connected speed limit check passed: True`. Open `qualification.json` in each evidence folder. Verify `passed`, `cleanup_passed`, and `source_files_match`. The runner checks that the original decision survives restart and that only one command receives a reservation.

| Case | Expected result |
|---|---|
| `operator_wins` | The eligible operator wins and an independent observer confirms the 4 m/s cap. |
| `thermal_fallback` | The operator's real-time deadline passes before sealing. The eligible thermal proposal wins and the observer confirms the 3 m/s cap. |
| `stale_fence` | The simulator presents a number older than the decision's generation. The service rejects it and both speed traces match. |
| `local_protection` | The bus already has a 3 m/s local ceiling. It refuses the winning 4 m/s request and both speed traces match. |

The fenced simulator sends the generation back to the service just before applying a command. The service checks that it matches the single reserved command for that run and bus. A matching generation still requires a valid deadline and a safe speed change. [Design decision](../adr/0050-command-authority-and-fencing.md)
