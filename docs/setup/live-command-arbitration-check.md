# Verify competing speed caps through live monitoring

Arbitration chooses one eligible request when several roles ask for different actions. Here, the operator requests 4 m/s, the thermal role requests 3 m/s, and the energy role requests 5 m/s. The simulated bus checks the winning decision's authority number before applying the cap. The observer measures what the bus actually did from the stored monitoring readings.

1. Open PowerShell in `<checkout>`. Use the previously built local simulator image. [Live command setup](live-speed-command-check.md)
2. Run a monitoring-only reference drive. Keep its evidence folder for the three comparisons. The runner checks its files and source fingerprints before starting each commanded drive.

   ```powershell
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --live baseline --evidence-dir evidence/p3/my-live-authority-reference
   ```

3. Run each case with its own results folder. `--live-authority` selects the competing-proposal scenario. The launcher gives each complete check up to 245 seconds, including startup and cleanup.

   ```powershell
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --live-authority operator_wins --reference-evidence evidence/p3/my-live-authority-reference --evidence-dir evidence/p3/my-live-authority-operator
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --live-authority thermal_fallback --reference-evidence evidence/p3/my-live-authority-reference --evidence-dir evidence/p3/my-live-authority-thermal
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --live-authority stale_fence --reference-evidence evidence/p3/my-live-authority-reference --evidence-dir evidence/p3/my-live-authority-stale
   ```

4. Expect three proposals, a single sealed decision and a normal live monitoring drive. Each run should print `Connected speed limit check passed: True`. In its `qualification.json`, check `selected_proposer`, `execution_state`, `accepted_originals`, and `cleanup_passed`.

| Case | Expected result |
|---|---|
| `operator_wins` | One eligible operator decision and an independently confirmed 4 m/s cap. |
| `thermal_fallback` | The operator's deadline passes before sealing. The thermal role wins, and the observer confirms 3 m/s. |
| `stale_fence` | The real authority check rejects the injected older number. The journal records a refusal before actuation and all speed readings match the reference. |

Every drive supplies 16 original messages and 320 readings to monitoring. The runner checks that the journal contains three proposals, one sealed decision, one queued command and one reservation. The stale case also verifies the journal has zero execution results and zero effect records for the refused command. [Engineering decision](../adr/0053-verify-live-command-authority.md)
