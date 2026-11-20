# Dashboard operation and implementation

DriveTwin's monitoring dashboard reads the Monitoring API. Its nine views share the sidebar, local Inter font, original icons, rounded surfaces, and chart palette. The sample preview remains a separate build entry point.

## Views

| View | Data and interactions |
|---|---|
| Overview | Trip progress, estimated distance remaining, the next passenger stop in the current forecast plan, charging review, four key readings, a selectable trend, and forecast detail |
| Fleet | Vehicle comparison with charge, progress, freshness, and trip status; selectable vehicles; expandable per-vehicle readings and metadata |
| Map | Actual paired coordinates, observed path, planned lane geometry, road labels, stops, station markers, layer selectors, follow, fit, and center controls |
| Measurements | All catalog-defined signals; group, search, component, quality, and freshness filters; raw units, timestamps, source, cadence, metadata, and history links |
| History | Any number of registered signal plots, a searchable checkbox menu, Battery and Motion shortcuts, a shared cursor, trip-event markers, recorded values, interval statistics, and bounded CSV/JSON downloads |
| Energy Forecast | Remaining route energy, measured/lowest/arrival charge, reserve outcome, forecast updates, input sources, energy accounting, saved records, and links to their readings and route |
| Activity | Charging decisions and observed effects seen by the current browser tab, links to readings and the map, source and quality events, and connection activity |
| System | A delivery summary, the delivery path, queue and ingestion statistics, storage observations, and rejection details |
| Operator or Charging Advisor | Charging review, advice or approval eligibility, observed charging, and profile-specific vehicle controls |

The twenty standard measurements are declared in the [catalog](../../catalog/monitoring-signals.json). Extensions enter through the same catalog-driven table. Summary cards convert fractions to percentages, m/s to km/h, joules to kWh, and kelvin to Celsius. Detailed tables and exports retain stored units.

## Data flow and state

The database commits original observations, sample rows, current state, and the receipt together. The Monitoring API returns a consistent snapshot and a scoped revision cursor. Server-Sent Events (SSE), an HTTP connection that delivers updates from the server, replaces the current snapshot approximately once per second. After an interruption, the browser marks readings stale and requests a fresh snapshot.

Quality describes validity. Freshness describes whether a reading is current. Source state describes the producer. Connection status describes the browser's connection to the API. A healthy source pause can retain valid readings while their observation age grows. Browser disconnection and source disconnection remain separate states.

History fixes its query to a snapshot revision. New arrivals do not alter an applied query halfway through pagination. Charts preserve missing/invalid gaps and source-epoch boundaries. Display reduction preserves extrema; downloads contain original readings and metadata. The UI label **Working time** refers to the source's elapsed timeline. Stored field names and export formats are unchanged.

Map coordinates come from accepted latitude/longitude pairs belonging to the same source event. Planned lane geometry and the observed path are separate layers. Fonts, icons, and map geometry are served locally. The local Montréal backdrop adds reviewed OSM streets, parks, water, and place names. Other areas retain their actual route geometry without invented background features. No external map tiles or sample coordinates enter the connected dashboard. Position cards show the exact UTC observation time and working time separately.

The route-aware Energy Forecast shows a measured starting charge and predicted lowest and arrival charges on a fixed 0–100% scale. The update chart compares saved forecast endpoints by working time and marks changes to the trip plan. An infeasible trip shows the energy or power shortfall. Stale or disconnected current inputs suppress current numerical results. Saved records remain available for review. An approval-enabled deployment accepts the operator key at dashboard sign-in. It grants controls for the configured vehicle. An ordinary monitoring key gives read access. Replay uses its own service, credentials, and playback state.

## History and forecast navigation

Open History from the sidebar to begin with no signals. Open **Select signals to compare**, search or browse the measurement groups, and check any registered signals. Each selection immediately adds a separate graph; unchecking it removes the graph. All twenty standard signals can be shown together, and catalog extensions use the same controls. The shared cursor aligns the selected graphs. Charge displays as percent and stored energy as kWh; expandable records, summaries, and downloads retain their declared stored units.

**Inspect battery readings** in Energy Forecast opens charge, stored energy, and speed around that record's cutoff. The cutoff marker identifies the calculation time; the surrounding history retains its original source and quality information. Selection remains editable. **View forecast route** displays the remaining geographic route saved with that exact forecast input. Missing geographic input produces an unavailable state. **Back to Energy Forecast** returns to the selected record.

The map has four rounded switches: observed trajectory, planned or forecast route, stops and stations, and map labels. Saved-forecast mode uses observations through the cutoff and identifies its historical context. Ordinary map mode retains vehicle following and current source state.

The live Map defaults to **Full trip**. Select **Last 30 working minutes** under **Trajectory window** for a shorter trail. Full trip loads committed fixes from departure and then requests only observations added since the last loaded snapshot. Late arrivals are placed in working-time order and gaps remain visible. The full view is bounded at one working day and 20,000 fixes. A failed refresh leaves the last loaded trail visible with a notice.

On prepared SUMO trips, Overview and Map show the distance remaining on the active driving route and the next passenger stop using the latest accepted source observation. These readings are available with Energy Forecast disabled. Charging bays and the final simulator hold are excluded from the passenger stop list. Ambiguous destination-road occurrences can make remaining distance unavailable. Estimated time to arrival still uses a current forecast or the estimate saved at charging approval.

The graph count has no application cap. Requests are processed sequentially in groups of at most five signals under one snapshot and time range. The browser checks snapshot and policy consistency between groups. Per-response, total-export, interval-summary, and timeout budgets remain enforced. A large selection can take longer to load; failed requests display an error without combining incompatible results.

## Implementation

| Source | Responsibility |
|---|---|
| [Live entry](../../apps/dashboard/src/main.tsx) and [monitoring client](../../apps/dashboard/src/monitoring/useLive.ts) | Authentication, authorized asset selection, snapshot lifecycle, and navigation |
| `DashboardShell.tsx`, `ui/`, and `style.css` | Shared presentation, local assets, charts, and responsive layout |
| `Measurements.tsx`, `Overview.tsx`, and `Fleet.tsx` | Catalog-based readings, summaries, and vehicle comparisons |
| `MapPanel.tsx`, `useMapScene.ts`, and `mapModel.ts` | Validated coordinates, Leaflet rendering, context, and trajectory gaps |
| `HistoryExplorer.tsx`, `Trend.tsx`, `historyModel.ts`, and `historyRequests.ts` | Historical queries, plotted observations, summaries, and exports |
| `RouteEnergyForecasts.tsx`, `useRouteEnergy.ts`, and `ForecastChargeTrend.tsx` | Route-aware result lifecycle, accounting, input provenance, saved records, and update chart |
| `forecastMap.ts` and `evdt.monitoring.forecast_map` | Read-only projection of the stored forecast route, bound to its input hash and source identity |
| `EnergyForecasts.tsx`, `EnergyDetails.tsx`, and `EnergyOutcome.tsx` | Original-model records and station approach results |
| `Operations.tsx` and `OperatorControls.tsx` | Operational views and the separately authorized command interface |
| `replayMain.tsx` and `replayModel.ts` | Isolated recorded playback |

## Development and verification

From `apps/dashboard`, use `npm run check`, `npm test`, `npm run build`, and `npm run format:check`. The dependency lock defines the installed versions. `npm run design:dev` serves the separate sample preview for rapid presentation work. [Preview guide](dashboard-preview.md)

For connected acceptance, use the [dashboard review procedure](dashboard-review.md). It builds the current service, starts one guarded bus, opens a manual browser, and runs an independent browser check with screenshots. Source inventories include nested shared components, styles, and font assets. Earlier release reports remain bound to their original revisions. [Connected dashboard verification](../verification/dashboard-connected.md)
