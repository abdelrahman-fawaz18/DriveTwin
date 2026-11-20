# Resume the live release checks

The launch check samples available Windows memory while Ubuntu and Docker are active. It requires 2 GiB throughout the roughly 35-second window. During a run, the controller also watches memory, disk space, and clock stability. The restart, restored-history, clean browser, and five-minute recovery checks passed on the documented host with the relevant safeguards active. [Release evidence](verification.md)

## Prepare the terminal

1. Open Windows PowerShell in the project root. The executor checks use the existing local images. Before the demo or history reader, prepare the exact dashboard image and charging map with the [demo preparation commands](demo.md#prepare-the-local-images).
2. Save any work in other apps. Keep the laptop plugged in and its lid open while a check runs.
3. Start one check at a time with a fresh evidence folder. Each command performs its own launch check. A precheck hold writes a report before any test services start.

## Reproduce command recovery

Run the coordinator case first. The coordinator is the service that records a command before handing it to the simulator. This case restarts that service while monitoring continues, then checks that the original reservation survives and a second handoff is refused.

After a restart, the runner asks Docker for the current port before checking readiness. A port is the local connection number the runner uses to reach the service. The passing coordinator report records 16 matching originals and refusal of a second handoff. The command below is available for reproducing that result with a fresh report folder.

```powershell
python scripts/release/qualify_restarts.py --case command-api --output evidence/p6/restart-command-api-external-01
```

The executor applies the command in the simulator. The next two commands interrupt it at different points and verify the outcome from the saved journal and speed readings. Run each after the previous case has passed and cleanup is complete. When the executor test replaces services, the monitor takes one fresh inventory if Docker removes a listed container before inspection. The same time and resource limits apply to the replacement sample.

```powershell
python scripts/release/qualify_restarts.py --case executor-before --output evidence/p6/restart-executor-before-external-01
python scripts/release/qualify_restarts.py --case executor-after --output evidence/p6/restart-executor-after-external-01
```

The controller allows up to 260 seconds for the worker after preflight. Expect some quiet time. Success prints `passed: True` and saves `qualification.json` with matching sources and verified cleanup. For a held or failed case, preserve the folder and review the report before starting another attempt. To interrupt a running check, press **Ctrl+C once** and allow cleanup to finish. [Earlier restart evidence](../verification/service-restart-status.json)

## Verify the clean demo and current history reader

- With the original sealed recording and its exact image available, run the [current reader procedure](../setup/history-query-and-restore.md#current-reader-compatibility). It uses a fresh isolated database and verifies permissions and query results. The restore guide explains what must stay with the private recording.
- Use the [clean-source setup](source-package.md) and [automated browser steps](demo.md#verify-the-browser-workflow) to reproduce readings, history, map, replay, reconnect, and the source-paused state from a clean checkout.

## Reproduce five-minute delivery recovery

Follow the [one-bus recovery procedure](../setup/sustained-outage-and-capacity.md#run-the-current-release-recovery-check). The bus saves messages to disk while delivery services are down. The test verifies a five-minute outage, a queue drain within 120 seconds, and exact delivery of every original message. The verified run cleared the queue 19.84 seconds after restoration began. [Recovery evidence](../verification/outage-delivery-compatibility.json)

After a run, retain the new `qualification.json`, logs, and cleanup record. The [release evidence table](verification.md) separates the checks that passed from those that still need a result.
