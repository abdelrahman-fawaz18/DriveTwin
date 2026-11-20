# Reproduce the static charging station check

This check answers whether the simulated bus has a legal road approach and enough lane space for the configured charging positions. It opens the pinned SUMO network without creating any vehicles or advancing the simulation. For example, the two-bay `west-c` stop spans 36 meters on a 128.3-meter lane. The check uses the modeled 12-meter bus length plus four meters of allowance per bay. [Station schema and policy](../charging-policy.md)

1. Open PowerShell in `<checkout>`. Use the existing Ubuntu Docker Engine and the `evdt-energy-collector:p2` image, which contains the pinned SUMO tools. The project launcher passes the command into Ubuntu from Windows PowerShell. [Launcher guide](linux-launcher.md) · [Simulator image setup](connected-speed-limit-command.md)
2. Run the check below. Use a new output filename on each attempt. The Docker options start one temporary container with no external network and a read-only project mount. A second mount allows only the private results folder to receive the report. `--timeout 120` bounds the complete launcher run in seconds.

   ```powershell
   New-Item -ItemType Directory -Path .runtime/p4-station-checks -Force | Out-Null
   python scripts/linux.py --timeout 120 -- docker run --rm --network none --read-only --memory 512m --tmpfs /tmp:size=128m,mode=1777 --mount type=bind,source=<checkout>,target=/workspace,readonly --mount type=bind,source=<checkout>/.runtime/p4-station-checks,target=/output -w /workspace -e PYTHONPATH=/workspace/src evdt-energy-collector:p2 /app/.venv/bin/python -m scripts.charging.check_station_profiles --output /output/my-station-check.json
   ```

3. Expect `Verified 3 hypothetical station profiles without driving.` The simulator's local control interface can briefly print a connection retry while SUMO starts. Success requires exit code 0 and `passed: true` in `.runtime/p4-station-checks/my-station-check.json`. The report gives each lane, stop length, map pin offset, directed distances, tool version, and fingerprints of the inputs. It should show zero vehicles and zero physical steps.
4. The container is removed on exit and the verifier deletes its temporary network. Keep the report locally for comparison. If configuration validation fails, check the printed station or field before making a new attempt. `--catalog` can point at another versioned configuration using a container-visible path. The checker rejects duplicate station IDs, reversed stop intervals, invalid lanes, stops outside their lane, insufficient space for bays, and a missing directed path in the declared check.

The checked distances connect the first mission edge to the stop and the stop to the final edge. They are graph checks. A later policy must pick a valid rejoin point without skipping required stops, and a reference drive must establish actual stopping and arrival energy. The `depot-a` check is intentionally short because both the start and the finish are on its edge. [Policy and scenario rules](../charging-policy.md#scenario-and-evidence-rules)
