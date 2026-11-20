# Command operations and recovery verification

The command service keeps the original request when an operator changes it. For example, if the operator replaces a pending 5 m/s cap with 4 m/s, the first command reads `superseded` and links to the second. If the simulator has already claimed the original, its reservation stays authoritative and the change conflicts.

| Operator action | Endpoint and body | Expected outcome |
|---|---|---|
| Cancel pending speed command | `POST /v1/commands/<id>/cancel` with `{}` | `200` and `canceled`. An identical retry returns the saved state. |
| Replace pending direct speed command | `POST /v1/commands/<id>/replace` with a new speed command | `200` and `superseded` for the original. `replacement_command_id` identifies the queued successor. |
| Read speed command | `GET /v1/commands/<id>` | The request's current state, reservation, result, and independent effect when available. |
| Record a station request | `POST /v1/mission-intents` with a versioned mission intent | `201` and `intent_recorded`. An exact retry returns `200`. |
| Read a station request | `GET /v1/mission-intents/<id>` | The original intent, recording time, and registered station catalog fingerprint. |

These routes require the operator credential. A change after handoff returns `409`. An unlisted station returns `422`. The mission admission deadline is at most 30 seconds. Its status means the request was saved for review. The simulator receives only speed commands through its existing private claim route.

From PowerShell in `<checkout>`, run each group using the previously built simulator image. Choose unused evidence folder names. The launcher runs each case in Ubuntu with an upper bound of 245 seconds. [Simulator setup](connected-speed-limit-command.md)

```powershell
foreach ($case in @('cancel','replace','cancel-race','replace-race','sealed-cancel','mission')) {
    python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --pending $case --evidence-dir "evidence/p3/my-pending-$case"
    if ($LASTEXITCODE -ne 0) { break }
}
```

The race tests start the operator change and simulator claim together. Either order is legal. The recorded winner determines which command, if any, the simulator can receive. The separate replacement drive must confirm 4 m/s from observations. The mission case checks an empty simulator queue before any speed command is issued.

```powershell
foreach ($case in @('direct','arbitration')) {
    python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --command-backup $case --evidence-dir "evidence/p3/my-command-backup-$case"
    if ($LASTEXITCODE -ne 0) { break }
}
```

Expect `Connected speed limit check passed: True` for each case. The backup reports should show `inventory_exact`, `source_and_occupied_restore_refused`, `old_credentials_denied`, `handoff_refused_after_restore`, and cleanup for both deployments. The database archive and complete reads stay in the private evidence folder. This is an offline local recovery exercise using a stopped source and an isolated target. It preserves the database records. The simulator starts a fresh run after restoration. [Recovery concepts](backup-and-restore.md)
