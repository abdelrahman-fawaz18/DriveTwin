# Compatibility and reproducible inputs

The local simulation release uses locked dependencies, pinned container images, explicit contract profiles, and source fingerprints. The release evidence records the configuration exercised by each check. [Release acceptance](release/verification.md)

DriveTwin is the public project name. `EV-DT` and `evdt` are the implementation identifiers in the workspace path, Python package, container labels, source archive, and recorded protocol metadata. These identifiers connect the code to its evaluated inputs.

## Runtime and build inputs

| Component | Recorded input | Reproduction scope |
|---|---|---|
| Application Python | Python 3.12 with the exact image and `uv.lock` | Monitoring, APIs, command services, Energy Workers, and shared checks |
| SUMO | SUMO, TraCI, sumolib, and sumo-data 1.28.0 | Road conversion, simulated motion, battery consumption, and charging |
| Database and broker | PostgreSQL 18.6 and Mosquitto 2.0.22 in the recorded images | Transactional storage, scoped roles, and MQTT 5 delivery |
| Frontend | React and React DOM 19.3.1, Leaflet 1.9.4, and locked TypeScript/Vite dependencies | Dashboard build and browser behavior |
| Browser checks | Playwright 1.64.1 with its pinned Chromium | Automated Windows browser qualification |
| Thermal analysis | Python 3.13.7, NumPy 2.4.3, and SciPy 1.17.1 | Separate laboratory and synthetic model studies |
| Thermal rule comparison | RTAMT 0.4.10 in its separate Python 3.12 environment | Independent comparison of the persistence rule |
| Host | Windows-native agent, Ubuntu 24.04 under WSL 2, and native Ubuntu Docker Engine | Project launcher and local Linux services |

Exact container digests are in the [application image manifest](../environments/dataflow-images.json) and [engineering image manifest](../environments/engineering-images.json). Python dependencies are in [uv.lock](../uv.lock). The [dashboard](../apps/dashboard/package-lock.json), [contract](../packages/contracts-ts/package-lock.json), and [browser](../qualification/browser/package-lock.json) locks record the JavaScript dependencies. [Thermal runtime](setup/thermal-reproduction.md)

## Contract and schema compatibility

The [portable profile](../contracts/profile.json) preserves telemetry schemas 1.0.0 and 1.1.0. The byte-frozen legacy reader requires the documented adaptation for newer optional annotations. Operational simulation tuples have their own explicit schema and catalog registry. Unknown tuples are rejected. [Contract verification](setup/contract-verification.md) · [Registry and quality](setup/run-registry-and-quality.md)

Replay preserves recorded envelope and recipe identity through a separate API and read role. The extension exercise adds a supported coolant scalar and second asset through explicit catalog and adapter changes. [Replay setup](setup/monitoring-start-view-stop.md) · [Extension proof](setup/sensor-and-asset-extension.md)

The service schema upgrade check exercises supported fixtures and explicit incompatibility refusal. SQL migrations are versioned and retain their original checksums. [Schema verification](verification/schema-compatibility.json)

## Forecast and command records

Route-aware forecast records bind authenticated observations, assignment, ordered route, model, numerical request and durable job claim. Its single-worker controller and restricted writer preserve immutable history; the dashboard rechecks source, route and freshness before presenting current guidance. [Route-aware model](models/route-aware-energy.md) · [Qualification](verification/energy-route-aware.md)

Legacy known-route records keep their original worker packaging, numerical and uncertainty labels, model release and input references. The interface labels original model history when a route-aware assignment is unavailable. [Historical forecast storage](setup/persistent-energy-forecasts.md) · [Advisory closeout](verification/energy-advisory-acceptance.md)

Speed and charging configurations share the durable command journal while using their own reviewed interfaces and observations. Reservations survive restarts and prevent a repeated claim. The charging configuration rejects competing command writes. [Command catalog](command-catalog.md) · [Registered charging](setup/registered-charging-and-dynamic-comparison.md)

## Source and evidence compatibility

A result applies to the source and inputs named in its report. Source guards compare exact bytes before reusing evidence. The current reader was separately checked against the sealed historical dataset and its original database image. Restoration preserved the full inventory, and current queries and exports passed. [History compatibility](verification/history-compatibility-audit.json) · [Release checks](release/verification.md)

The installed geographic catalog uses the separately supplied [portable input bundle](../simulation/geographic/README.md); installation verifies all 168 file identities. Source archives omit private bundle data, recordings and fitted artifacts. Historical Git-based inspectors require the local development history and cannot be treated as fresh-public-checkout tests. [Preparation boundaries](release/source-package.md)

The one-bus 30-minute pass, five-minute recovery, scoped restart cases, and clean-source browser checks support the local release. Earlier ten-bus memory stops remain recorded. The GitHub Actions workflow calls the shared checks on Ubuntu, and its hosted execution awaits publication. [Engineering checks](setup/engineering-checks.md) · [Workloads](workloads-and-metrics.md)
