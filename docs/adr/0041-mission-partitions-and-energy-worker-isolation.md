# ADR-0041: Whole-mission partitions and isolated energy execution

Date: November 4, 2026. Status: selected for P2-B1. Measured acceptance is recorded separately in `docs/verification/energy-boundary.json`.

## Responsibility and scope

Use the existing Docker Engine and locked Python base to run a one-request advisory energy worker. This adds an execution boundary around the existing calculations, with no new framework, broker, database, public port, persistent service or monitoring interface. Windows-native agent mode and the canonical source tree remain unchanged.

Separate harness-only mission design and evaluation access from worker inputs. The worker image contains only the package initializer, numerical core, selected-observation logic, advisory functions, strict job interface and worker entry point. It excludes monitoring/plant code, dataset partitions, experiment recipes, simulation libraries, credentials, project scripts and source-tree mounts.

P2-B1 qualifies the partition design/guards and this local worker boundary. It does not claim that the mission data has been collected, a deployed forecasting API is authorized, input admission has become authenticated, or a model has passed accuracy/uncertainty targets. P2-B2/C1/D1/E1 retain those responsibilities.

## Whole-mission partition design

`qualification/energy/mission-design.json` reserves 24 planned missions: 12 train, 6 validation and 6 held out. Each of six regimes has two training realizations, one validation realization and one held-out realization: nominal, high load, stop-heavy, cold/high auxiliary demand, capacity mismatch, and low charge/high auxiliary demand. Every recipe declares its units through named fields, independent realization seed, initial charge, loaded mass, auxiliary demand, temperature, actual/declared capacity, traffic label and stop duration. They are experiment hypotheses, not real-bus specifications.

All use the existing hypothetical Montreal core route. This design tests unseen complete realizations on a known route; it does not establish unseen-route or interacting-traffic generalization. Reserved seeds and traffic labels are design inputs, not proof that a generator has implemented the regime. The next collection step must document exactly how each recipe maps to actual simulated behavior and preserve base map/code/configuration hashes.

`parse_design` requires complete per-regime split coverage and unique mission IDs. A causal family, identical recipe or realization seed cannot cross partitions. A causal family means one underlying trip and all of its derived windows, copies, replays or parameter variants that share the same realization. Those remain together even if a file or run identifier changes. Independently realized trips may share the known route and regime.

`validate_inventory` binds future collected artifacts to the frozen design, family, split and recipe digest. It rejects incomplete datasets when completion is required, duplicate artifact IDs, wrong assignments and identical content across partitions. Content fingerprints must be independently computed from causal traces and observed values without collection IDs, filenames or paths; declared digests alone do not prove honest collection. Unit fixtures exercise the guard; no real collected inventory is claimed here. Previously inspected P1/P2 development recordings must not be relabeled as held-out missions.

Training data may fit parameters. Validation data may select settings, supported regimes and D-18 thresholds. Held-out data is reserved for the final report after those choices. The purpose gate refuses a wrong partition and requires calibration, target and protocol digests before held-out evaluation. These are required references; the future evaluation controller must verify their actual frozen artifacts. The Python guard is not a filesystem permission or a defense against a dishonest host administrator. No held-out outcomes are generated or scored in this increment.

## Minimized request boundary

`make_job` projects an already admitted, caller-authorized observation window into four required canonical quantities plus their scope/time/event identity. It strips unrelated sensors, annotations, manifest metadata and other original fields. A future or wrong-scope packet rejects the request before calculation. This projection is a derived input view; it never rewrites the original telemetry or claims its selected digest is the original wire digest.

The strict `isolated-energy-job/1.0.0` input has only version/method, scope, clock, mission, policy, bindings, selected observations, declared estimator parameters, assumed motion plan and integration step. Nested records also use exact field lists. There are no arbitrary metadata bags, module names, file paths, URLs or command fields. Unknown fields, duplicate JSON keys, invalid encoding, nonfinite values and excess work are rejected. Invalid jobs return a generic rejection without echoing input contents. Valid but unusable observations return the existing unavailable forecast, distinct from a valid predicted deficit or zero charge.

Fixed worker limits are 1 MiB/request, 1,000 observation points, 1,000 motion intervals and 100,000 numerical steps. Existing model versions, canonical units, sensor bindings and numerical behavior remain unchanged. Supplied policy cannot raise these worker ceilings. Changing this boundary requires versioned compatibility review and requalification.

## Runtime restrictions and trust model

`energy-worker-sandbox/1.0.0` fixes an ordinary user, read-only root filesystem, all Linux capabilities dropped, no new privileges, Docker's default syscall filter, private process/interprocess namespaces, 256 MiB memory with no additional swap allowance, one CPU, 32 processes and a 30-second controller deadline. No project/host mount, Docker socket, published port or external network is supplied. Only one bounded request enters through standard input; the result returns through standard output. Each execution uses the exact built image ID throughout the calculation. The controller inspects actual Docker configuration, checks no extra environment was injected and compares packaged code digests with reviewed source.

Linux capabilities are individual administrator powers; dropping all removes those powers. The no-new-privileges setting prevents gaining them through another executable. The default secure-computing filter restricts system calls. Docker's `none` network has only a private loopback interface. These controls supplement minimized packaging; they do not create a virtual machine or remove trust in the host kernel/engine. [Container settings](https://docs.docker.com/engine/containers/run/), [default syscall filter](https://docs.docker.com/engine/security/seccomp/), [isolated network](https://docs.docker.com/engine/network/drivers/none/).

The host, Docker administrators, reviewed image/controller and upstream admission/configuration owner are trusted. A host administrator can bypass isolation; malicious numeric inputs can encode information despite field names; inference from legitimate measurements cannot be prevented by a filesystem boundary. In particular, ideal energy/SOC observations may reveal capacity algebraically. This increment blocks direct private-state access, not statistical inference or an information-theoretic guarantee. Capacity mismatch, measurement realism and independent calibration remain essential.

The caller still declares source clock/state, scope, charging exclusion and forward assumptions. This boundary does not authenticate those declarations or prove a supplied future plan was independently estimated. P2-D1 must bind them to trusted asset-scoped inputs/configuration before live product integration. No worker output authorizes a command.

## Qualification and cleanup

Create four synthetic private files and HTTP endpoints in a separate harness container on a uniquely owned internal network, plus an ignored host canary. Positive controls first read the files/endpoints from the harness. Then arbitrary probe code, running with the predictor's exact restrictions, attempts those reads, the host path, Docker socket and a process-root path. It also checks forbidden imports, actual user/capability/syscall/network state, packaged bytes and read-only filesystem behavior. A successful probe is evidence about those real attempts in this local configuration, not every possible escape technique or future deployment.

Run reference/planned jobs, stale/future unavailable cases and hidden-field rejection through the real worker. Compare exact parsed results with the existing calculation for the same normalized job. Verify the reusable command independently. Keep raw requests, outputs, logs, IDs and canaries ignored. Cleanup checks ownership before stopping/removing only test containers and its empty test network; compare original container/network/volume inventories afterward. No global prune, engine restart, host-memory setting or application workload is needed.

Preserve compatible P1 and P2-A evidence after exact source checks. Run focused tests during implementation and the final shared checks after stabilization. P2 accuracy, complete mission convergence, uncertainty, storage/API/UI and final release gates remain open.
