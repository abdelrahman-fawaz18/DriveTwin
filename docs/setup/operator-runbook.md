# Vehicle operator runbook

Use this guide to reproduce a local speed cap, read the outcome, and recover safely after an interruption. For example, a bus can slow to 4 m/s even if the simulator's reply is lost. The command timeline shows the saved handoff, the reply if available, and the measured speed effect separately. [Command catalog and permissions](../command-catalog.md)

## Start and verify a disposable session

1. Open Windows PowerShell in `<checkout>`. Use the existing Ubuntu Docker Engine, pinned simulator image, and Windows browser dependencies. Docker Compose starts the services together in a named project. The browser check uses Playwright, which opens Chromium and performs the same actions as an operator. [Linux launcher](linux-launcher.md) · [Simulator image](connected-speed-limit-command.md) · [Browser setup](browser-verification.md)
2. From PowerShell, run the following command with an unused evidence folder name. It starts the temporary monitoring and command services, builds the dashboard, and runs the three operator cases.

   ```powershell
   python scripts/commands/qualify_operator_browser.py --evidence-dir evidence/p3/my-operator-browser-check
   ```

3. Expect a replacement with a confirmed 4.00 m/s effect, a conflict after reservation with an unknown execution outcome, and cancellation of a sealed decision. The test also saves a station intent and checks that monitoring and replay credentials cannot submit actions. In the top-level `qualification.json`, expect `passed: true` and passing `confirmed`, `unknown`, and `sealed` cases. Their `services/qualification.json` files should report `cleanup_passed: true`. Keep detailed browser and service output under the ignored evidence folder. [Browser check](operator-dashboard-check.md)

## Operate a prepared local deployment

1. Prepare one registered run and bus and the separate private service credentials described in [local command setup](connected-speed-limit-command.md). Combine the monitoring and command Compose files and the operator overlay. Choose the direct profile for manual caps or the arbitration overlay for a sealed decision. Start the database and initializers through Compose, then the monitoring API and `command-api`. Keep the one-off simulator stopped until the operator and command service are ready. For an interactive source feeding normal monitoring, use the live speed overlay and its registered source configuration. [Live speed profile](live-speed-command-check.md)
2. Open the prepared dashboard at its local address. Sign in with the scoped operator key, select the registered bus, and open **Operator**. The key stays in page memory until sign-out or reload. Confirm the displayed run and bus before issuing an action.
3. In the direct profile, queue a cap such as 4 m/s while the run is ready for its bounded handoff. Start the prepared one-off simulator before the wall deadline. To withdraw or change a still-pending cap, use **Cancel** or **Replace**. If handoff wins the race, the change conflicts and the timeline refreshes. In the arbitration profile, the dashboard can view and cancel the pending sealed decision. The separate proposal and seal endpoints prepare that decision. [Authority check](command-authority-check.md)
4. Read the timeline in order. A queued request passed admission. A reservation means the simulator took or may have taken it. An acknowledgment is the simulator's response. A confirmed effect comes from a separate observer comparing recorded speed with the reference drive. Run the configured observer with the command ID and the complete reference and commanded readings before treating the effect as confirmed. In the live profile these readings come through the normal monitoring API. A recorded station intent means a destination request was saved for review. [Observed speed check](live-speed-command-check.md)

## Recover and stop

| Situation | Action |
|---|---|
| Submission response is lost | Look up the original ID. If retrying submission, send the exact original request with the same ID. |
| Cancellation or replacement conflicts | Refresh the timeline and check the original ID and any successor. A reservation stays closed. |
| Reservation has an unknown execution outcome | Keep the reservation. Compare the independent speed readings when the observation window is complete. Preserve an unknown execution state even if the measurements confirm the effect. |
| Source exits during a run | Keep the old database records and queue. Inspect any partial readings. Provision a fresh run with its own identity, queue, and scoped credentials. The live adapter refuses to resume the interrupted run. [Restart check](live-source-restart-check.md) |
| Database must be recovered | Stop the source and restore the archive into a separate empty cluster with new credentials. Check inventories, representative reads, and that closed handoffs stay closed before using the restored records. [Backup and restore check](command-operations-check.md) |

Allow the disposable runner to finish and clean up its own containers, network, and volume. If interrupted, allow its cleanup to finish and treat the run as incomplete. For a prepared deployment, stop the source and command services using the same explicit Compose project and file list used to start them. Preserve the database and queue if recovery or later inspection is needed. Use the cleanup inventory to check that the intended temporary resources are gone. [Monitoring shutdown and cleanup](monitoring-start-view-stop.md#stop-and-check-cleanup)
