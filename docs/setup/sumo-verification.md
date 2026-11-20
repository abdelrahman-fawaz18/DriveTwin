# SUMO verification: the first moving bus

This guide explains the road-simulator portion of **P0-02**. Phase 0 checks that our tools work together; Phase 1 builds the bus monitoring system. This experiment uses an independently authored artificial road and a bus-shaped vehicle. It does not establish the accuracy of an electric-bus model or provide a Montreal map.

## The tools and their jobs

**SUMO** means **Simulation of Urban MObility**. It calculates movement of individual vehicles along connected roads. We use its `sumo` executable in **headless** mode, meaning without a graphical window. Automated tests can therefore run through the existing Windows-to-Ubuntu command launcher. SUMO's graphical viewer is optional and is not part of this check. [Official SUMO overview](https://eclipse.dev/sumo/docs/FAQ.html#what_is_sumo)

**TraCI**, the **Traffic Control Interface**, is the connection through which our Python test program talks to the running simulator. In this test, Python asks SUMO to advance one step and reads position, speed, distance, and journey events. The two programs exchange messages over a local connection inside the test container. This is the foundation for a future simulator adapter, not an implemented vehicle command service. [Python interface documentation](https://eclipse.dev/sumo/docs/TraCI/Interfacing_TraCI_from_Python.html)

**netconvert** builds a SUMO road network from road descriptions. We write simple source files; it generates the more detailed `network.net.xml` file that SUMO uses. We do not hand-edit that generated network. [SUMO network source format](https://eclipse.dev/sumo/docs/Networks/PlainXML.html)

**XML**, Extensible Markup Language, is a text format using named tags such as `<node .../>`. SUMO uses it to describe roads, vehicles, routes, and configuration. These are simulator input files, separate from the JSON measurement contract used by the backend.

| File | Plain-language role |
|---|---|
| [nodes.nod.xml](../../simulation/reference/nodes.nod.xml) | Three road endpoints/junctions at 0, 500, and 1,000 meters |
| [edges.edg.xml](../../simulation/reference/edges.edg.xml) | Two directed road sections joining those points |
| [routes.rou.xml](../../simulation/reference/routes.rou.xml) | One bus, its driving parameters, road sequence, departure, and scheduled stop |
| [scenario.sumocfg](../../simulation/reference/scenario.sumocfg) | Which files to load, time step, random seed, and gridlock behavior |
| [expectations.json](../../simulation/reference/expectations.json) | Explicit limits against which the test judges the resulting journey |
| [sumo_probe.py](../../scripts/platform/sumo_probe.py) | Runs SUMO through TraCI and checks actual movement |
| [sumo.py](../../scripts/platform/sumo.py) | Builds the environment, runs the probe, preserves evidence, and checks cleanup |

## Understand the small scenario

```text
start -------- road-a, 500 m -------- middle -------- road-b, 500 m -------- finish
                 stop at 250 m
```

The inputs below are **provisional test assumptions**, not measured specifications of a real bus:

| Setting | Value and meaning |
|---|---|
| Vehicle | `bus-001`, SUMO bus class, length 12 m |
| Roads | Two straight connected sections; one lane each; bus access permitted |
| Vehicle maximum speed | 10 m/s, equivalent to 36 km/h |
| Road speed limit | Approximately 13.89 m/s, equivalent to 50 km/h |
| Acceleration / comfortable deceleration | 1.2 m/s² / 4.5 m/s² |
| Scheduled stop | Bus front reaches 250 m along the first lane; stop duration 5 s |
| Simulation step | 0.2 s |
| Random seed | 42; driver-imperfection and speed-deviation settings also fixed |
| Maximum journey time | 180 simulated seconds; failure if the bus does not finish |
| Coordinates | Local Cartesian meters, with no geographic projection |

A **time step** is one calculation interval. Python advances the simulation by 0.2 simulated seconds, then reads the state after that update. SUMO owns movement calculations. Python owns test advancement and collection. **Simulation time** is the virtual world's clock; **wall time** is how much real time the computer spends running the test. They are recorded separately. A journey taking over a minute in the virtual world can be calculated in much less real time.

A **random seed** initializes the simulator's random choices. Keeping the seed, inputs, and software fixed helps reproduce an experiment. This test compares the actual recorded trajectories from two fresh SUMO processes. It establishes repeatability on this pinned platform, not numerical identity across every architecture or future SUMO release. [SUMO randomness](https://eclipse.dev/sumo/docs/Simulation/Randomness.html)

The reported position is the center of the vehicle's front bumper. The bus therefore starts with its front already a vehicle length into the lane; its observed traveled distance is slightly less than the geometric one-kilometer road length. The lane also has a lateral offset from the source road centerline. These are normal coordinate conventions, not GPS noise. **GPS**, Global Positioning System, provides geographic location; this fixture's perfect local `x/y` coordinates are not GPS measurements. Geographic conversion and a sensor-error model are later adapter work. [SUMO position convention](https://eclipse.dev/sumo/docs/TraCI/Vehicle_Value_Retrieval.html)

## What must pass

The probe builds the road network separately for each run and starts a fresh SUMO child process. A **child process** is a program started by another program; here Python starts SUMO and waits for it to exit.

1. The installed simulator, TraCI, helper library, and SUMO data must all report version 1.28.0. Both `sumo` and `netconvert` must start successfully.
2. Exactly the expected bus departs and arrives before the simulated-time deadline.
3. Python reads over 100 position/speed/distance records, at the specified 0.2 s intervals, with finite numeric values.
4. The bus traverses both road sections, respects its speed limit, moves continuously within the per-step distance bound, and accumulates distance without regression.
5. The scheduled stopped interval falls within the declared discrete-step tolerance; the final traveled distance falls within the declared interval.
6. No collision or teleport event occurs; input conversion and simulation produce no warnings; SUMO's independent trip output confirms a completed journey.
7. Both canonical numeric trajectories have the same SHA-256 fingerprint. A fingerprint summarizes file content; matching fingerprints here check the complete serialized measurement sequence, not just its final position.
8. Both SUMO processes exit successfully and are reaped, meaning Python retrieves their exit results. The uniquely labeled test container is removed. No named Docker volumes are created.

SUMO can **teleport** a vehicle to recover from gridlock or other problems. That means relocating it without normal driving. We disable the gridlock recovery setting and also check teleport events, so recovery cannot silently appear as a successful journey. This setting does not globally disable every possible teleport cause; the event check remains necessary. [Teleport behavior](https://eclipse.dev/sumo/docs/Simulation/Why_Vehicles_are_teleporting.html)

## Reproduce it from Windows

Follow these steps to verify the simulator setup.

1. Open a PowerShell terminal in `<checkout>`. The existing [Linux launcher](linux-launcher.md) and [native Ubuntu engine](docker-engine.md) must already be ready.
2. Run this command with a **new** evidence directory name:

   ```powershell
   python scripts/linux.py --timeout 1200 -- python3 scripts/platform/sumo.py qualify --evidence-dir evidence/p0/my-sumo-check
   ```

   `--timeout 1200` sets a 20-minute outer allowance for an initial build/download. The individual scenario run has a shorter timeout and a simulated-time deadline. `qualify` builds and verifies the environment. `--evidence-dir` saves the report and raw artifacts; the harness refuses to overwrite an existing directory.

3. Expect tooling checks, a container build, and two scenario runs. Building may need internet access to fetch the recorded packages. The actual simulation container runs without external network access; TraCI uses its internal loopback connection. It has a read-only filesystem, an output-folder mount, temporary scratch space, an ordinary user, and fixed CPU/memory limits.
4. Open `qualification.json` under the chosen evidence directory. Success requires `passed: true`, two passing runs, identical trajectory fingerprints, and no remaining owned containers. The report identifies the input files, package versions, image, commands, and source fingerprints used.
5. Inspect `artifacts/run-1/trajectory.json` to see each reading. `tripinfo.xml` is SUMO's journey record. `netconvert.log` and `sumo.log` explain simulator warnings or failures. Repeat-run files live under `run-2`. `commands.log` records the build and container output, and `debian-packages.tsv` inventories the installed container packages.

If a run fails, keep that evidence and correct the cause before rerunning into a new directory. The first observed failure is documented below. Do not reset Docker to troubleshoot this fixture.

Normal completion and scoped cleanup are checked. Host power loss, app termination during a container run, and interrupted-run cleanup have not been qualified. If interrupted, identify the exact container and run labels in `.runtime/p0-sumo` before targeted cleanup; do not prune unrelated resources.

## Installation and reproducibility choices

The official `eclipse-sumo` Python package supplies compiled simulator executables. `traci` supplies the Python client, `sumolib` supplies helper functions, and `sumo-data` supplies supporting simulator data. All four are fixed at 1.28.0 in a [separate dependency declaration](../../simulation/reference/pyproject.toml) and [lockfile](../../simulation/reference/uv.lock). This preserves the already-qualified backend's dependency files. Python remains the same pinned 3.12.16 container base. [Official Python package installation](https://eclipse.dev/sumo/docs/Downloads.html)

A **shared library** is compiled functionality loaded by a program when it starts. The first minimal-image run installed the Python packages successfully but could not start the SUMO executable because `libX11.so.6` was absent. A diagnostic also identified missing X extension/rendering, OpenGL, atomic-operation, and XML-parser libraries. These are required by this packaged binary even though this test opens no graphical window.

The [container recipe](../../containers/sumo.Dockerfile) now installs the required Debian packages: `libx11-6`, `libxext6`, `libxrender1`, `libgl1`, `libatomic1`, and `libexpat1`, plus their declared dependencies. This changes the test image, not Ubuntu's system packages. [Fixed package sources](../../containers/sumo-debian.sources) use Debian snapshots dated November 3, 2026. A **snapshot** is a date-fixed package archive. The base images, Python packages, and package archive are all fixed, and installed package versions are captured in evidence. The snapshot configuration follows Debian's documented expiry handling while retaining signed-archive verification. [Debian snapshot usage](https://snapshot.debian.org/)

The first failed report is retained at [verification-01](../verification/README.md). Changing the recipe and rerunning is part of qualification; accepting an install-only check would have missed this dependency.

The [corrected verification](../verification/README.md) passed both runs. Each yielded 566 vehicle-state records, a 113.2 s trip, a 5.0 s scheduled stop, and a maximum speed of 10 m/s. The numeric trajectories matched exactly. Simulation runtime was 0.4717 s and 0.4613 s, measured after network generation and excluding image build/container launch; these are tiny-test observations, not a fleet-performance benchmark.

SUMO's trip record reports 988.00 m of completed route length. The last sampled distance is 987.228 m because the bus leaves the active vehicle list when it arrives; there is no final active-vehicle sample exactly at the destination. The test driver's clock ends at 113.4 s after the arrival event, while the trip record timestamps arrival at 113.2 s. Interpret the final sampled value and the arrival record against their respective timestamps. [Trip-output semantics](https://eclipse.dev/sumo/docs/Simulation/Output/TripInfo.html)

The harness's `lock` action generates the separate SUMO lockfile; `format` runs the project's locked Ruff formatter. Ordinary reproduction uses `qualify`, which checks style without changing source files. Dependency or base-image upgrades are intentional changes requiring a fresh qualification report.

## Boundary with the actual digital twin

The standalone qualification establishes repeatable SUMO motion and Python access. The [operational uplink guide](operational-uplink.md) documents its integration with the queue, broker, PostgreSQL, and dashboard.

The simulation's authoritative state is experiment truth. In Phase 1 an adapter will turn it into explicitly identified observations, with timing and sensor behavior defined separately. Driving the simulator's clock is test control, not evidence of a digital twin making an operational decision.

See [ADR-0004](../adr/0004-sumo-qualification.md) and [Phase 0 status](../foundation-status.md) for measured acceptance and remaining decisions.
