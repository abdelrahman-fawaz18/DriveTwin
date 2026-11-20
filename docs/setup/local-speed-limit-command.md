# Run the first simulated bus command

A **command contract** defines the information a command must carry and the rules it must pass. This version lets an operator set a speed cap on one simulated bus. A cap of 4 meters per second is 14.4 kilometers per hour. The simulator can still drive more slowly where the route requires it.

| Check | What it does |
|---|---|
| Coordinator | Checks the command format, operator, bus and run identities, capability, value and unit. |
| Executor | Rechecks the same rules at execution and refuses to raise the simulator's existing limit. |
| Real and simulated time | The command must arrive within both windows. The demo allows 10 seconds of real time and accepts the command between simulated seconds 4 and 6. The time windows govern acceptance. The applied cap stays in effect for the rest of this test drive. |
| Execution result | `acknowledged` records a successful simulator response. `unknown_outcome` records an uncertain attempt. Exact duplicates within one process and validity window return the saved result. |
| Effect check | Compares the bus's `motion.speed` samples against a second run of the same route. The check reads the measurements separately from the execution result. |

The operator is the person running the local command. The simulator instance belongs to that process and has its own run identity. It operates inside an isolated container and writes its measurements to a local evidence folder. The motion exercise uses a short prefix of the reviewed route and the project's speed sensor. The connected dashboard and broker command path are later Phase 3 work.

1. Open PowerShell in `<checkout>`. The command below uses the previously built `evdt-energy-collector:p2` image, which packages the project's pinned SUMO simulator and Python tools. Create the local evidence folder if needed.

   ```powershell
   New-Item -ItemType Directory -Path evidence/p3 -Force
   ```

2. Run the baseline and command cases. `--limit-m-s` is the requested cap. `--reason` is recorded with the command. `--output` must be a fresh folder. Choose a new name for a repeat. Docker gives the container read-only access to the project and write access to the evidence folder. The launcher starts Docker inside Ubuntu and allows 120 seconds for the command.

   ```powershell
   python scripts/linux.py --timeout 120 -- docker run --rm --network none --read-only --memory 512m --memory-swap 512m --cpus 1 --pids-limit 32 --cap-drop ALL --security-opt no-new-privileges:true --tmpfs /tmp:size=128m,mode=1777 --mount type=bind,source=<checkout>,target=/workspace,readonly --mount type=bind,source=<checkout>/evidence/p3,target=/evidence -w /workspace -e PYTHONPATH=/workspace/src evdt-energy-collector:p2 python scripts/commands/run_speed_limit.py --limit-m-s 4 --reason 'Local speed cap check' --output /evidence/my-speed-limit-run
   ```

3. Look for `Speed limit check passed: True`. Open `evidence/p3/my-speed-limit-run/qualification.json` and check both the acknowledgment and the observed effect. The two `observations.json` files show what the speed sensor reported. The command and execution result are saved separately in `commanded/`. If a run fails, keep its evidence folder and use a new folder for another attempt.

The request and execution result each have a versioned schema, a file that lists the allowed fields and values. The engineering decision records who may issue the command and when the simulator can apply it. [Request schema](../../contracts/commands/speed-limit-1.0.0.schema.json) · [Execution result schema](../../contracts/commands/execution-result-1.0.0.schema.json) · [Engineering decision](../adr/0046-first-local-simulation-command.md)

The reviewed run applied a 4 m/s cap at simulated second 4. Four earlier samples matched exactly. Among 11 later comparison pairs, two showed the baseline exceed the cap while the commanded run respected it. Maximum sampled speeds were 8.33 m/s and 4.00 m/s, respectively. The full result is recorded separately from the original private measurements. [Verification record](../verification/speed-limit.json)
