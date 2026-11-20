# Route-aware Energy Forecast dashboard

The connected dashboard implements the reviewed Energy Forecast, Overview, Map, and History layouts. The numerical predictor, telemetry contract, command permissions, and replay isolation are unchanged.

## Implemented behavior

| Area | Behavior |
|---|---|
| Energy Forecast | Pink route-energy card; fixed 0–100% measured/lowest/arrival charge comparison; reserve warning; energy accounting and input provenance; forecast-update chart; saved-result navigation |
| Current result states | Current and below-reserve results display valid numerical values. Infeasible results identify energy/power shortfall and suppress an achievable arrival estimate. Stale or disconnected results suppress current values while retaining saved records. |
| Overview | Existing trend signal/window controls, reading count, timestamps, and raw records; concise forecast summary with a link to detail |
| Map | Locally served OSM streets, parks, water, and names; four rounded layer switches; exact UTC observation time; separate working time; saved remaining route bound to its forecast input |
| History | No graph-count cap; searchable signal checkboxes; one chart per selection; shared cursor; forecast-cutoff marker; relevant editable defaults from Energy Forecast; empty sidebar entry |
| Data handling | Sequential groups of up to five signals retain the API request bound. All groups share the same snapshot and range. Raw downloads, source continuity, missing-data gaps, byte/row/time limits, and interval-summary limits remain enforced. |

## Verification

- The bounded shared suite passed **1,091 Python tests**, **201 subtests**, **44 frontend tests**, strict type/style checks, and **339 portable contract cases**. All **81 operational contract parity cases** also passed.
- The built application passed an offline desktop browser review at **1600 × 1000**. It checked all twenty selected charts, empty History, Overview controls, saved-record pagination and navigation, four map switches, reserve warnings, infeasible outcomes, and stale current suppression. Eleven screenshots were retained privately. The browser fixture uses retained geographic inputs; it is display evidence, not an additional model-accuracy evaluation.
- Focused service tests verify missing-geography refusal, exact forecast/input binding, reader authorization, and stale diagnostic suppression.
- Candidate privacy and secret scans passed. Raw browser fixtures, screenshots, and runtime reports remain ignored.

The connected attempt was **blocked before launch** by the existing host preflight: minimum available Windows RAM was **1.70 GiB**, below the **2 GiB** requirement. Its inventory found zero running containers; no bus, browser, or service workload was started. The attempt remains recorded as blocked, with no automatic retry. The subsequent external run passed with the retained terrain manifest; the blocked attempt remains unchanged.

### Connected acceptance — November 13, 2026

The external review passed on implementation revision `cc92bd5`. Recomputed fingerprints match all **432 source/configuration files** and the retained terrain manifest. The independent cleanup inspection found no remaining containers, networks, or volumes belonging to this test.

| Check | Result |
|---|---|
| Source and forecast lifecycle | One SUMO bus; 135 accepted messages, 20 signals per message, 2,700 readings. First forecast at working second 7; 10 completed jobs, zero failed jobs. |
| Energy presentation | Matching charge values/colors in cards and saved records; stale current values suppressed; saved history retained; current forecast restored after source interruption and changed assumptions. |
| History | All twenty graphs selected together; complete raw JSON export across four bounded requests; interval summaries; empty sidebar entry and forecast-specific defaults. |
| Forecast map | Remaining geometry bound to the saved request, source, and cutoff; four switches; replay credential and unauthorized asset access refused. |
| Host and cleanup | Existing resource safeguards passed. Minimum available Windows memory: 4.32 GiB. Source consistency, controller shutdown, service cleanup, and independent cleanup inspection passed. |
| Screenshots | Seven retained browser captures cover current, saved, stale, recovered, History, and Map views. Desktop viewport: 1600 × 1000; the History detail capture uses its element bounds. |

One complete prediction comparison was recorded. Other comparison windows were skipped after source/route changes or remained incomplete at the bounded stop. They are not additional accuracy passes. The controller's `operator_interrupt` stop reason records the qualifier's deliberate shutdown signal after its checks completed.

[Machine-readable acceptance summary](energy-dashboard-acceptance.json) records source and report fingerprints. Earlier release and numerical study results retain their original source and measurement scope.

## Operation and boundaries

[Dashboard operation](../setup/dashboard.md) describes signal selection, units, exports, and forecast links. [Route-aware service operation](../setup/route-aware-energy.md) describes configuration and scheduling.

The new read-only route endpoint is `GET /v2/energy-forecasts/{fence}/map?asset_id=...`. It requires the existing asset-scoped monitoring reader credential. The server joins the saved result to its immutable request, validates their fingerprints and source identity, and projects the remaining geographic route. Replay credentials and unauthorized assets are refused. No route is substituted when saved geography is unavailable.

The richer background covers the reviewed central Montréal snapshot. A different map retains its supplied route geometry and requires its own attributed geographic background for equivalent cartographic detail. The forecast model remains independent of the dashboard backdrop.

Original-model records keep their own presentation. Scenario-range improvements, charging-policy adoption, Operator redesign, and ten-bus endurance remain outside this dashboard change.
