# ADR-0004: SUMO installation and movement qualification

Date: November 3, 2026 (America/Toronto)

Status: Passed for P0-02 movement/TraCI qualification. [Two-run evidence](../verification/README.md) and [combined P0-02 acceptance](../verification/README.md) record the result. D-06's project-wide matrix and D-09's production time/energy decisions remain open.

## Decision

Use released SUMO 1.28.0 with matching `traci`, `sumolib`, and `sumo-data` 1.28.0 Python packages for the P0-02 headless movement test. The version is a released candidate, not a nightly branch. Use the existing pinned Python 3.12.16 and uv image references, a separate SUMO dependency lock, and a dedicated container recipe. Keep all source in the canonical project tree. [Release record](https://eclipse.dev/sumo/docs/ChangeLog.html#version_1280_08102026), [Python installation](https://eclipse.dev/sumo/docs/Downloads.html)

This method requires no system-wide SUMO installation, additional administrator service, graphical desktop, WSL-agent switch, or duplicate source checkout. An Ubuntu package-manager installation and the project's official container images remain alternatives if this candidate cannot pass the required checks. Do not select an older Ubuntu package or a moving image tag merely because installation is shorter.

## Observed dependency issue

The initial [verification report](../verification/README.md) confirms package installation but an executable-start failure due to a missing `libX11.so.6`. The other missing libraries were identified with `ldd`, the Linux tool that lists an executable's shared-library dependencies. The recipe installs the required libraries inside the container from fixed, signed Debian package snapshots. Full installed-package inventory is exported with the successful/failed probe artifacts. This is an explicit build dependency correction; no recurring manual repair is part of running the scenario.

## Scope and evaluation

The independently authored scenario uses three nodes, two roads, one bus, and a scheduled stop. Its inputs and expectations are versioned under `simulation/reference`. Parameters are synthetic qualification assumptions. No third-party scenario source or map was copied.

The test requires two fresh network builds and SUMO processes, actual TraCI reads, correct departure/arrival, bounded continuous movement, scheduled stopping, no collision/teleport, clean process exit, and identical canonical numeric trajectories. A fixed random seed and 0.2 s step are test parameters. SUMO advances its movement state when the test driver requests a step; no wall-clock pacing is imposed. This does not close D-09's production scheduling, energy ownership, convergence, or conservation decisions.

The [guide](../setup/sumo-verification.md) defines exact reproduction steps and limits. Reports must distinguish Python/package installation, executable startup, numeric scenario checks, repeatability, and cleanup. Existing data-flow evidence is a separate prerequisite; this does not pretend to integrate the simulator with the backend.

Package metadata identifies `EPL-2.0 OR GPL-2.0-or-later` for the four selected Python packages; observed expressions are preserved in probe evidence. This is an inventory of declared upstream terms, not a project license choice or a completed license inventory of bundled binaries/system dependencies. D-16 remains open for distribution/reuse review. The local scenario is independently authored. Original DriveTwin work now uses the [MIT license](../../LICENSE).

## Remaining decisions

Observed result: two fresh SUMO processes each produced 566 vehicle-state records, completed the trip in 113.2 simulated seconds, and stopped for 5.0 simulated seconds. Their canonical numeric trajectories matched exactly. There were no collision or teleport events, conversion/simulation warnings, or nonzero child exits. Normal owned-container cleanup passed. The test-driver clocks ended at 113.4 s after the final step; trip arrival/duration is SUMO's separately recorded 113.2 s.

- Final project-wide version matrix and supported platforms, including later architecture upgrades.
- Montreal road artifact, rights, accessibility, route/mission assumptions, and real bus parameters (D-07/D-16).
- Energy/battery owner and model integration; authoritative production simulation clock and step-size studies (D-09).
- Adapter sampling/noise, semantic observation mappings, and monitoring integration in Phase 1.
- Full resource/workload measurements and interrupted-run cleanup qualification.
