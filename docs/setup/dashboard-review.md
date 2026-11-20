# Connected dashboard review

The review runs the built DriveTwin dashboard against one paced SUMO bus. It checks observation delivery, browser updates, history, map rendering, source interruptions, browser reconnection, and isolated replay. A separate browser window supports manual inspection during the same drive.

## Run from Windows

Close unused applications, connect external power, and keep the laptop lid open. Keep this guide available in a text editor. In Windows PowerShell:

```powershell
Set-Location '<checkout>'
python scripts/dashboard_review.py
```

Use `python scripts/dashboard_review.py --check-only` to verify the committed source, profile, and pinned browser tools without a build or workload. The normal command runs one attempt. It does not retry a failed check.

| Stage | Expected behavior | Typical duration |
|---|---|---|
| Preparation | Check memory and disk space; build the current source-bound dashboard image | 45-second precheck, usually 1–5 minutes for the build; a cold cache can take longer |
| Service startup | Repeat the host check and start isolated local services | 1–2 minutes |
| Live inspection | Print the monitoring address and reader key, copy the key, and open the browser | About 5½ minutes, including the deliberate interruption |
| Recording inspection | Print the replay address and separate reader key, copy it, and open replay | 2 minutes after automated browser checks finish |
| Cleanup | Remove only the review's containers, networks, and volumes; release sleep prevention | Usually under 1 minute |

Allow about 10–20 minutes overall. If the command is still running after 30 minutes, press **Ctrl+C once** and leave the terminal open for cleanup. Do not shut down Ubuntu. The sleep request expires after 35 minutes even if its helper loses contact; no power-plan setting is changed.

## Browser login and navigation

When the monitoring page opens, click **Reader credential**, press **Ctrl+V**, and click **Connect to monitoring**. The launcher has already copied the credential and printed it under **MONITORING LOGIN** in the terminal. If the clipboard changes, select and copy the text after **Reader credential:**, then paste it into that browser field. It is held in browser memory and sent in authorization headers.

Navigate among the nine tabs normally. The automated browser uses its own session and cannot change the tab selected in the manual window. Browser history queries freeze the interval when applied; use **Use latest time**, then **Apply historical query** to include later readings.

After the drive and automated checks finish, the review tool opens the authorized replay page. Set **From working seconds** to `0` and **To working seconds** to `300`, click **Load recording**, and use **Play**.

Leave the terminal running throughout the two-minute replay window. It finishes and cleans up automatically. Pressing Ctrl+C before completion records an incomplete attempt. Escape is not a completion command. The temporary keys expire with the review services; keep terminal captures containing them private.

During the live drive, a short intentional pause leaves the bus stationary and marks readings **paused**. The subsequent source interruption marks them **stale**. New readings resume after reconnection. A separate network interruption affects only the automated browser. At the end of 300 observations, live readings stop advancing; recorded history and replay remain available until cleanup.

This profile starts monitoring and replay services. Energy displays its current unavailable state when no qualified forecast has been persisted for the run. Operator controls retain their separate credential and deployment requirements. Positive energy-output and command-execution checks use their dedicated profiles.

## Captures and checks

The browser saves three complete passes through the nine tabs at working times of approximately 45, 135, and 225 seconds. Additional captures cover pause, source disconnection and recovery, browser disconnection and resynchronization, mobile navigation, history, replay, and the complete observed route. A complete run produces at least 40 PNG files and an HTML gallery. Images record actual application state; no sample responses are substituted.

The service checks reconcile all 300 original messages and all 6,000 readings with stored history. Map context retains the same run and vehicle identity. Replay returns the original packets in a separate playback namespace, and authentication tests reject cross-role access and replay commands.

The versioned [review profile](../../qualification/dashboard/review.json) preserves the release memory, timing, storage, source-fingerprint, and cleanup guards. This is a functional and visual review. It does not establish sustained-load or fleet capacity.

## Inspect the result

The terminal prints a unique `evidence/p6/demo-dashboard-*` directory. It contains:

| File | Purpose |
|---|---|
| `operator-result.json` | Preparation, overall result, independent cleanup check, and sleep release |
| `qualification.json` and `worker.json` | Controller and Linux service results, source compatibility, and guards |
| `browser-result.json` | Browser assertions and capture manifest |
| `screenshots.html` | Local image gallery; open it in any browser |
| `screenshots/` and `screenshots.json` | Captures and their names and timestamps |
| `ready.json` | Temporary local addresses and credentials; keep private |

A successful terminal result reads **DASHBOARD REVIEW PASSED; EVIDENCE REVIEW PENDING**, with cleanup and sleep release both true. A failed or incomplete result retains the evidence. Inspect the saved service, browser, source, and cleanup reports before accepting the result or starting another attempt.

## Energy and Operator service checks

The populated Energy and Operator views use separate bounded fixtures. Run them sequentially from Windows PowerShell in the project folder. Choose new evidence directory names for each attempt:

```powershell
python scripts/known_route_energy/qualify_energy_product.py --presentation-only --evidence-dir evidence/p2/my-dashboard-energy-review
python scripts/commands/qualify_operator_browser.py --evidence-dir evidence/p3/my-dashboard-operator-review
```

Run the second command after the first reports success and cleanup. Both use the existing 2 GiB host reserve, continuous resource sampling, pinned Chromium, and owned-resource cleanup. A host precheck failure starts no application services. Keep the terminal open through cleanup. Review a failure before retrying.

The Energy check verifies the frozen model and retained training inputs, admits 61 controlled observations through the existing fixture, runs the isolated forecast workers, and serves the persisted result through the Monitoring API. Chromium checks the displayed estimate against the API value, the endpoint chart, both station assessments, stale suppression, saved history, connection recovery, and vehicle selection. It does not retrain a model, reopen the reserved evaluation, or repeat backup qualification. Its captures are saved under the chosen directory's `browser/` folder.

The Operator check exercises replacement with independently measured effect, an unresolved handoff, and cancellation of a sealed request. It checks reader/replay denial and run/vehicle scope, then captures the command lifecycle at desktop and mobile widths. Each case saves its own service report, browser result, captures, and host samples. [Operator procedure](operator-dashboard-check.md)

Expected completion is `integration passed: True` for Energy and `Operator browser qualification passed: True` for Operator, with passing cleanup in their `qualification.json` files. Allow several minutes for each check, including resource prechecks and image preparation. The browsers run automatically; no login or manual navigation is required.
