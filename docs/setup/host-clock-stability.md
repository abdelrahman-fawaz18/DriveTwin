# Host clock stability checks

The clock that shows the time of day can be corrected by a time service. The elapsed-time clock acts like a stopwatch. DriveTwin compares them so that the source, database, and browser reports can be placed on a reliable timeline. A sudden change in their relationship stops qualification.

The first external smoke attempt detected a 1.793-second Ubuntu clock step about 22 seconds into warmup. The Ubuntu journal shows multiple clock changes during the attempt, including the recorded interruption. At the time, both Hyper-V time synchronization from Windows and Ubuntu's independent network time service were active. The Windows Time service also reported that its own clock was unsynchronized when checked afterward. These observations establish a plausible conflict. The journal does not identify the process that made the exact adjustment. [External attempt review](../verification/monitoring-smoke-external-review.json) · [Canonical's Ubuntu on WSL time guide](https://ubuntu.com/wsl/docs/stable/explanation/time-sync/)

The user-initiated Windows sync advanced the last successful sync time. Three direct comparisons with the configured server were within 2 milliseconds. A 150-second clock-only check passed with Ubuntu's extra service still active. [Diagnostic record](../verification/host-clock-diagnosis.json)

**Post-change verification passed.** After the Ubuntu service was disabled, both clocks passed a fresh 150-second check with 151 paired samples. The maximum departure was 0.00845 milliseconds for Windows and 0.000379 milliseconds for Ubuntu against the 10-millisecond limit. Ubuntu's service was inactive and disabled before and after the probe, and Windows-to-Ubuntu synchronization was still enabled. The subsequent live retry passed the unchanged clock guard throughout the run. [Post-change evidence](../verification/host-clock-readiness.json) · [Verified live retry](../verification/monitoring-smoke-pass.json)

## Correct the time setup

1. In Windows, open **Settings → Time & language → Date & time** and select **Sync now**. Wait for confirmation. If Windows reports a failure, keep the Ubuntu time service active and bring back the error. [Windows Time service reference](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/Windows-Time-Service-Tools-and-Settings)
2. After Windows syncs, open a direct Ubuntu terminal from PowerShell.

   ```powershell
   wsl.exe --distribution Ubuntu-24.04
   ```

   In that Ubuntu terminal, run the following command and enter the Ubuntu password there if prompted.

   ```bash
   sudo systemctl disable --now systemd-timesyncd.service
   ```

   This stops Ubuntu's separate network time service and prevents it from starting at the next Ubuntu boot. Hyper-V still supplies Windows time. Verify with `systemctl is-active systemd-timesyncd.service` and `systemctl is-enabled systemd-timesyncd.service`. Expect `inactive` and `disabled`. To restore the prior Ubuntu configuration if needed, use `sudo systemctl enable --now systemd-timesyncd.service`.

## Verify without running the bus

From Windows PowerShell in `<checkout>`, run:

```powershell
python scripts/check_host_clock.py --seconds 150
```

The probe keeps a lightweight Ubuntu process active and takes 151 paired readings over 150 seconds. It checks Windows and Ubuntu separately using the existing 10 millisecond clock-step limit and 1.5-second maximum sample gap. It also brackets each Ubuntu reading between two Windows timestamps to show their approximate agreement. No bus, browser, or Docker fixture is started.

Expect `"passed": true` for both clocks and the overall result. The raw readings and result are saved in ignored evidence storage. A stable clock window is a prerequisite for timing-sensitive qualification. The live runner also applies its source, memory, and scheduling guards. [Smoke procedure](monitoring-smoke.md)
