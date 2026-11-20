# Check a speed cap through normal monitoring

The simulated bus already samples its speed and adds it to a durable outbox, a queue that keeps readings until the receiver confirms storage. The normal sender passes those messages through the local broker to the ingestion service. The monitoring API serves the stored readings to dashboard clients. This check uses that same path to establish whether the bus slowed down.

1. Open PowerShell in `<checkout>`. Use the previously built local simulator image. [Connected command setup](connected-speed-limit-command.md)
2. Run the reference drive first. `--live baseline` starts the ordinary monitoring source for 16 samples. Expect `Connected speed limit check passed: True` after it verifies live snapshots and compares every stored payload with the original.

   ```powershell
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --live baseline --evidence-dir evidence/p3/my-live-speed-baseline
   ```

3. Run the commanded drive in a new evidence folder. `--reference-evidence` supplies the validated observations from the first run. The runner checks the reference fingerprints before it starts services, then enables the command overlay and sends a 4 m/s cap.

   ```powershell
   python scripts/linux.py --timeout 245 -- python3 scripts/commands/qualify_connected_speed_limit.py --live commanded --reference-evidence evidence/p3/my-live-speed-baseline --evidence-dir evidence/p3/my-live-speed-commanded
   ```

4. Expect progress for live snapshots and effect verification, then `Connected speed limit check passed: True`. Open `qualification.json` and look for `attempt_sim_s` equal to `4`, `accepted_originals` equal to `16`, and `independent_speed_effect.confirmed` equal to `true`. Both runs remove their temporary services and volumes. Each `--timeout 245` sets an upper bound in seconds for the full run including cleanup. The live portion takes 16 simulated seconds paced in real time.

The observer reads accepted speed measurements from the monitoring API. It compares the recorded reference trace with the commanded trace and submits both to the command service. The test also reads before and after snapshots while the source is running. These are the dashboard's data endpoints. The browser and operator command interface have separate checks. [Engineering decision](../adr/0051-observe-live-speed-commands-through-monitoring.md)
