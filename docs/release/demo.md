# Local electric bus demonstration

The monitoring demo runs one simulated bus and the ordinary dashboard services. You can watch current readings, open the mission map, inspect stored history, and replay the recording. The control examples use separate short sessions with their own command permissions.

## Prepare the local images

Open Windows PowerShell in the project folder. Use the documented Windows and Ubuntu setup first. Docker packages the services into images. The preparation command builds the dashboard from the pinned input versions and verifies the required charging network. A cold build may download the recorded dependencies. [Windows and Ubuntu setup](../setup/windows-wsl.md) · [Input and archive guide](source-package.md)

```powershell
python scripts/configure_local.py
python scripts/linux.py --timeout 1000 -- python3 scripts/release/prepare_simulation.py --dashboard
```

## View monitoring and replay

1. Start a new session. Pick a new output name if the example directory already exists.

   ```powershell
   python scripts/release/qualify_restarts.py --case demo-view --output evidence/p6/demo-my-session
   ```

   Expect about a minute for the host and service startup checks. Wait for `Demo ready`. If the command reports a failed precheck, it leaves a report without starting the services.

2. From a second PowerShell window in the project folder, paste the following as one line and press Enter within 45 seconds of `Demo ready`. It copies the temporary credential to the clipboard, signals the simulator to start, and opens the browser.

   ```powershell
   $demo = Get-Content -Raw evidence/p6/demo-my-session/ready.json | ConvertFrom-Json; Set-Clipboard -Value $demo.monitoring_token; New-Item -ItemType File -Path evidence/p6/demo-my-session/start | Out-Null; Start-Process $demo.monitoring_url
   ```

3. Click **Reader credential**, press **Ctrl+V**, and click **Connect to monitoring**. PowerShell copies the credential silently, so paste it before copying anything else. The bus sends 16 original messages with 20 readings each. Open **Measurements**, **Map**, and **History** to follow one value and its saved readings.

   Messages arrive about once a second for this short drive. Watch the observation time or a signal such as speed to see fresh readings arrive. Small latitude changes can round to the same displayed value. After the last message, the dashboard holds that value and its age keeps increasing. **Good** describes the reading's quality, and **Stale** describes how old it is.

4. Once the source finishes, the services stay available for up to 60 seconds while you inspect the recording. Run the line below, press **Ctrl+V** in **Replay reader credential**, and click **Connect to replay**. Its playback clock follows the saved simulation, while monitoring shows the source has finished.

   ```powershell
   Set-Clipboard -Value $demo.replay_token; Start-Process $demo.replay_url
   ```

5. To end viewing early after the source finishes, create `finish`. The worker stops and removes its own services. To interrupt during startup or driving, press **Ctrl+C once** in the starting window and let cleanup finish.

   ```powershell
   New-Item -ItemType File -Path evidence/p6/demo-my-session/finish
   ```

The console and `qualification.json` record the outcome. A successful run has `passed: true` and verified worker cleanup. The credentials and raw recordings stay in the ignored output folder. The browser uses credentials in request headers.

## Run the quick automated check

```powershell
python scripts/release/qualify_restarts.py --case demo-check --output evidence/p6/demo-monitoring-check
```

This starts the same application, checks the built assets and real service responses, and closes the session. The automated source steps through the same model without waiting for wall time. Once all messages are stored and the source queue is empty, the check stops delivery and verifies the completed recording through the dashboard and replay services.

The automated service check passed for all 20 current signals and histories, map context, and 16 original replay messages. Startup took 9.23 seconds with the engine and images available. The first memory-stopped attempt and the passing retry are recorded separately. [Demo evidence](../verification/local-demo-status.json)

## Verify the browser workflow

From Windows PowerShell in a prepared checkout, use the pinned Playwright installation and Chromium binary from the [browser setup](../setup/browser-verification.md). Playwright runs Chromium without opening a visible window. It connects to the local dashboard before starting the bus and enters the temporary credentials within the test.

```powershell
python scripts/release/qualify_restarts.py --case demo-browser --output evidence/p6/demo-browser-my-session
```

Use a fresh output folder. The test checks new live observations, a healthy pause past the freshness deadline, a stale display during lost source contact, and fresh readings after source recovery. It also interrupts the browser connection and verifies resynchronization, then opens map, history, and isolated replay. A pass requires the browser and service checks, source matching, host checks, and cleanup. Inspect `qualification.json` for the result and `browser.log` for progress. The output folder is private.

The clean-source browser run passed all ten checks and reconciled 16 messages. The run used pinned Chromium from a dedicated PowerShell session. [Browser evidence](../verification/source-reproduction.json)

## Reproduce the charging comparison

After preparing the local images and map, run this command from the project root in Windows PowerShell. Change `release-demo-comparison` if the folder already exists. It uses the pinned simulator inside a single temporary container and runs the same six missions as the recorded comparison. Each policy sees the same charger power at departure.

```powershell
python scripts/linux.py --timeout 260 -- bash -c 'docker run --rm --network none --read-only --memory 512m --tmpfs /tmp:size=128m,mode=1777 --mount "type=bind,source=$PWD,target=/workspace,readonly" --mount "type=bind,source=$PWD/evidence/p4,target=/output" -w /workspace -e PYTHONPATH=/workspace/src evdt-energy-collector:p2 /app/.venv/bin/python -B -m scripts.charging.run_dynamic_charging_comparison --matched-departure --output /output/release-demo-comparison'
```

Expect six `mission_arrived` results. The saved `qualification.json` lists each policy's actual charger, travel delay, reserve check, and charge delivered. The clean checkout reproduced every recorded outcome. [Comparison results](../verification/demonstration-review.json)

## Follow a command to its measured effect

After the monitoring session exits, run an isolated charging case. The operator request is authorized, claimed, and checked against simulator observations of delivered energy and resumed motion.

```powershell
python scripts/combined_faults/qualify_combined_faults.py --output evidence/p6/demo-charge --cases charge
```

Use fresh output names. Run `stale-outage` separately to see why an old request is refused when a charger loses power and communication returns. Run `cool` separately to compare matched simulated temperatures with and without the cooling boost. The measured charging and cooling results and their limits are in the [combined fault guide](../setup/charging-and-cooling-checks.md).

On the machine that holds the retained charging evidence, the command below reviews the new comparison alongside the saved operator-command trace. It follows the command ID through the charging, target, and resumed-motion observations and recomputes the energy delivered. It reads only existing reports.

```powershell
python scripts/release/verify_demonstration.py --comparison evidence/p4/release-demo-comparison/qualification.json
```
