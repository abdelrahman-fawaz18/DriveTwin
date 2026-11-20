# Use and verify the local operator dashboard

The Operator tab shows what was requested, whether the simulator took the command, what it reported, and what the measured speed showed. For example, a replacement cap can reach 4 m/s while the original request remains visible as superseded. When the simulator takes a command and its result is missing, the timeline displays an unknown outcome.

Start the local monitoring and command services with `ops/compose/compose.operator.yaml` added to the Compose files. Build the monitoring dashboard from the current source so it includes the Operator tab. Sign in at the dashboard with the scoped operator key, then select the vehicle and open Operator. The direct profile offers a speed cap between 2 and 5 m/s for the qualified simulation window. A queued cap can be canceled or replaced. In the arbitration profile, a pending sealed decision can be canceled. The mission form records a request for a registered station. [Local command service setup](connected-speed-limit-command.md)

From PowerShell in `<checkout>`, run the browser qualification with the existing Windows browser tools and Ubuntu simulator image. Use a new evidence directory.

```powershell
python scripts/commands/qualify_operator_browser.py --evidence-dir evidence/p3/my-operator-browser-check
```

The test builds the dashboard, creates temporary services for each case, and uses Chromium to send actions through the actual dashboard. Expect a passing replacement with a measured 4.00 m/s effect, a conflict after the simulator claims a pending command, and a cancellation of a pending sealed decision. The test also verifies that monitoring and replay credentials cannot submit an operator action. Each case applies the existing Windows memory precheck and continuous Windows/Ubuntu resource guards. Screenshots and detailed logs are private evidence. The first November 5 run stopped at a Playwright project argument error and remains recorded as failed. [Operator gateway decision](../adr/0055-scoped-operator-dashboard.md)

Desktop and mobile captures include the command lifecycle and command history. An interrupted handoff must retain an unknown outcome until independent evidence is available. A command acknowledgment alone does not establish its effect. The runner preserves unsuccessful attempts and does not retry automatically.

For the Phase 3 review, run the shared repository checks and pass their private report along with the passing browser folder to the auditor.

```powershell
python scripts/dev.py check
python scripts/review_command_acceptance.py --browser evidence/p3/my-operator-browser-check --shared .runtime/dev-checks/CHOOSE_REPORT_ID/report.json --output evidence/p3/my-phase3-closeout.json
```

The audit checks the current browser and service source fingerprints, the saved Phase 3 requirement evidence, and the frozen Phase 2 advisory release artifacts. Its Phase 4 section records the command integrity, advisory, runtime model, station endpoint accuracy, and automatic dispatch gates separately.
