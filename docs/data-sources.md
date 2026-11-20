# Data sources, provenance, and reuse boundaries

This inventory records the origin, transformations, units, and rights of incorporated data. Original project code and synthetic scenarios use the [MIT license](../LICENSE). Geographic and laboratory data retain the terms identified below.

## Incorporated geographic data

| Field | Record |
|---|---|
| Dataset / role | OSM-derived Montreal road subset; road geometry, graph connections and geographic coordinate checks |
| Public source | One bounded read-only Overpass query against the endpoint recorded in [source.json](../data/public/montreal-core-v1/source.json) |
| Query | [Versioned query](../simulation/montreal/roads.overpassql), including nearby turn-restriction relations and their members |
| Artifact | [Reviewed road snapshot](../data/public/montreal-core-v1/roads.osm.xml), 679,082 bytes: 3,661 nodes, 820 ways, 177 relations |
| Date / integrity | Actual access/database timestamps and SHA-256 content fingerprints in the source receipt; verify before import |
| Rights | Open Database License (ODbL) 1.0; preserve OSM credit and applicable data/derived-database conditions |
| Transformation | Keep motor-road categories/explicit bus overrides, complete geometry and associated relations; retain selected transport tags; remove account/contributor metadata and unrelated point details |
| Coordinates / units | WGS84 longitude/latitude in degrees; SUMO converts to a recorded projected meter system. Longitude/latitude order is explicit |
| Missingness / limits | Snapshot may omit/change roads or conditions; imported permissions/restrictions/signals may be incomplete. Import warnings are reviewed, not hidden. No real traffic, timetable, bus energy/health or station availability |
| Split | Fixed integration fixture, not independent calibration/held-out evidence of model accuracy |

Primary terms: [OSM copyright](https://www.openstreetmap.org/copyright), [ODbL legal text](https://opendatacommons.org/licenses/odbl/1-0/), and [OSMF attribution guidelines](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines). OSM distinguishes data from produced works; review the applicable requirements for each future exported data/map format. A code license does not replace the geographic data's terms.

Do not use the editing API or a public Overpass endpoint as the running product's live backend. The current fixture is offline after its one-time acquisition. Public query services have separate usage limits and availability constraints. The source receipt records the successful bounded acquisition and the reviewed snapshot. [Overpass usage guidance](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html)

## Geographic Energy Forecast inputs

The geographic energy study also uses regional OSM PBF extracts supplied by [BBBike](https://download.bbbike.org/osm/bbbike/) for Montréal, Ottawa, and Vancouver. Bounded road corridors retain bus access tags, turn restrictions, structures, and mapped passenger stops. Public NRCan high-resolution ground and surface mosaics provide elevation. Raw inputs, download receipts, derived networks, height corrections, and per-file fingerprints remain in ignored evidence and download storage. The [route and elevation guide](data/route-elevation.md) describes these transformations; the [geographic verification record](verification/energy-geographic.md) identifies evaluation groups. OSM contributor attribution and NRCan source attribution appear in the local map atlas.

## Dashboard geographic backdrop

The [Montréal backdrop](../data/public/montreal-backdrop-v1/README.md) is a reviewed display subset of the BBBike OSM extract used by the geographic study. It contains public road geometry/names, park/water polygons, and place labels. [Source fingerprints](../data/public/montreal-backdrop-v1/source.json) identify the input and derived snapshot. It carries ODbL-1.0 terms and OpenStreetMap contributor attribution. It contains no vehicle readings or forecast outcomes. Runtime route and vehicle layers remain separate authenticated data.

## Project-owned inputs

The bus parameters, hypothetical station definitions, workload samples and scenario logic are independently authored design inputs. Values are labeled assumed and do not identify real vehicles, employees, customers or infrastructure. The route's geographic edge selection derives from OSM and retains the data attribution/rights boundary. The [assumptions document](model-assumptions.md) explains calibration/operational limits.

No private company dataset, prior-project folder, personal map collection, physical bus data, public transit schedule or third-party training scenario was imported. Model observations are not made by joining unrelated timestamps into a fictional measured vehicle history.

## Future candidate data — not incorporated

| Candidate | Potential role | Required before use |
|---|---|---|
| Transit-provider static schedule feed | Stops, route/service context and timetable | Exact snapshot, provider license/attribution, identifiers/time zone and permission review; schedule is not battery telemetry |
| Original-publisher drive cycle | Operating-demand/plausibility case | License/file hashes, vehicle class, sampling and mission relevance |
| Relevant motor/battery testbench data | Independent calibration/held-out evaluation | Original terms, chemistry/component/scale/temperature, units/calibration, complete-session splits and transfer limits |
| Real vehicle or hardware observations | Stronger validation | Explicit access/reuse rights, privacy review, calibration/time synchronization and supported operating envelope |
| Public map tiles or imagery | Optional dashboard background | Provider-specific usage/attribution/cache terms; no tile/imagery download is included in P0-06 |

Listing a candidate grants no permission to use it. Keep unreviewed downloads ignored; review before promoting a public artifact. A newly incorporated source needs a data card, source/transform fingerprints, terms, units, quality/missingness, split and limitations.

## Motor testbench data

P5-A1 retained the original Paderborn *Electric Motor Temperature* archive under ignored downloads and audited its exact CSV. The [motor data card](setup/motor-data-audit.md) records publisher attribution, declared terms, units, 69 session blocks, timing limitations, and transfer boundaries. The original readings stay local. The data is available for planning a testbench prediction study. The profile split, model fitting, and synthetic fault evaluation remain separate checkpoints.

## Software inventory

The second simulated bus reuses the same reviewed Montreal OSM input and the existing derived route geometry, with a different hypothetical mission/stop configuration and independently authored assumed vehicle/plant/sensor parameters. It adds no external vehicle dataset or manufacturer specifications. Original OSM attribution and the current rights/release boundaries apply to the reused geographic material. The [extension guide](setup/sensor-and-asset-extension.md) distinguishes source configuration, simulated observations and physical-validation limits.

[Rights inventory](rights/README.md) separates installed software's declared terms from data rights and project ownership. It records the locked Python/npm/SUMO packages, tool/container sources, notice locations and release-review tasks. A metadata scan is not a complete audit of every bundled native binary or operating-system library.

## Synthetic energy development corpus

P2-B2 collected twelve complete training and six validation missions from the existing SUMO 1.28.0 source on the reviewed hypothetical Montreal route, plus one repeatability run. This is project-generated simulation data, not operator/manufacturer measurements or an external energy dataset. At the B2 checkpoint, the six reserved final outcomes had not been generated. C1 consumption and results are recorded separately below. Frozen recipes, current driver/sensor assumptions and evaluation settings live in `qualification/energy/mission-design.json`, `development-protocol.json` and `uncertainty-selection.json`.

The accepted corpus has 16,382 original admitted observations / 327,640 scalar readings and 81,916 physical steps. Separate per-file inventories retain original packet bytes, physical truth, scenario/import/model inputs and noise/seed provenance under ignored evidence. Only reviewed aggregate metrics and frozen artifact fingerprints are versioned; raw coordinates, traces and fitted templates remain private. Existing OSM attribution/import limitations remain applicable. This corpus supports a synthetic known-route study, not real-bus calibration or population coverage. See the [candidate card](models/energy-calibrated-candidate-card.md) and [learning guide](setup/energy-calibration-and-validation.md).

## Reserved synthetic energy evaluation corpus

P2-C1 generated the six reserved whole missions once after frozen-release and exact validation-parity checks. They add 5,431 contract-admitted original events / 108,620 readings and 27,155 physical steps. All 72 original collection files / 55,130,881 bytes were reverified; the combined 24-mission assignment/content guards passed. No duplicate held-out trial or parameter tuning was performed. This remains a known-route project-generated synthetic study, with unchanged OSM attribution/import and measurement assumptions. Raw coordinates/packets/truth and the consumed attempt claim remain private; [reviewed outcomes](verification/energy-heldout.json) and [sealed references](models/energy-held-out-release.json) are versioned.
