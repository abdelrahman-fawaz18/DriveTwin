# Tools and dependencies

DriveTwin uses Python services, a React/TypeScript browser interface and PostgreSQL storage. The local platform is Windows with Ubuntu through the project launcher and the native Ubuntu Docker Engine. Dependency locks and image fingerprints define the installed versions; [rights inventories](../rights/README.md) record notices.

| Area | Tools and purpose | Versioned inputs |
|---|---|---|
| Simulation | SUMO advances roads, bus movement, battery and charging; TraCI connects the adapter; netconvert prepares networks | [Simulator manifest](../../simulation/reference/pyproject.toml), [simulation dependencies](../rights/simulation-dependencies.json) |
| Geography | OpenStreetMap roads and stops; attributable raster elevation; optional pyosmium extraction; Rasterio/NumPy preprocessing | [Data sources](../data-sources.md), [terrain requirements](../../environments/energy-geospatial-requirements.txt), [OSM requirements](../../environments/osm-preparation-requirements.txt) |
| Telemetry | Python sqlite3 for the vehicle queue; Paho MQTT and Mosquitto for transport; Psycopg and PostgreSQL for storage | [Application manifest](../../pyproject.toml), [Python lock](../../uv.lock), [monitoring Compose](../../ops/compose/compose.monitoring.yaml) |
| APIs and control | FastAPI/Uvicorn/Starlette; distinct read, writer, operator, executor and observer scopes | [Architecture](../architecture.md), [command catalog](../command-catalog.md) |
| Energy | Project physics-based movement and energy code; isolated one-request container worker; trusted durable scheduler | [Route-aware model](../models/route-aware-energy.md), [controller](../../scripts/route_aware_energy/controller.py) |
| Browser | React and React DOM, TypeScript, Vite, CSS, Leaflet maps | [Dashboard manifest](../../apps/dashboard/package.json), [lock](../../apps/dashboard/package-lock.json) |
| Contracts | JSON Schema; Python jsonschema and TypeScript Ajv; explicit legacy adaptation | [Portable profile](../../contracts/profile.json), [verification](../setup/contract-verification.md) |
| Thermal studies | NumPy/SciPy for independent bench fitting and synthetic studies; RTAMT for the separate temporal-rule comparison | [Thermal reproduction](../setup/thermal-reproduction.md) |
| Engineering | uv/pip and locked environments; pytest/unittest/Node tests; Ruff, mypy and tsc; Playwright Chromium; Gitleaks and project privacy guard | [Engineering checks](../setup/engineering-checks.md), [browser setup](../setup/browser-verification.md), [privacy](../privacy.md) |
| Packaging | Git records reviewed local source; source archive verifies every committed blob; Markdown/Mermaid/SVG document interfaces and results | [Source procedure](../release/source-package.md), [figure builder](../../scripts/build_engineering_figures.py) |

The numerical route-aware worker uses the Python standard library. Geospatial preparation and scientific study dependencies remain outside that worker. The legacy Energy Worker image and model harness are retained for historical forecast and stationary charging compatibility.

Run Linux commands from the Windows project terminal through `python scripts/linux.py -- COMMAND`. The [runtime topology](runtime-topology.md) identifies services and ports. The saved GitHub Actions workflow invokes local engineering checks; a hosted result requires an authorized remote and actual execution. Setup and cleanup do not imply permission to change host settings, publish source or launch new qualification workloads.
