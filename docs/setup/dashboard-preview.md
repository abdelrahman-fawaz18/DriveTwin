# Interactive dashboard preview

The DriveTwin design preview provides nine interactive tabs and a separate recording replay. It runs entirely on sample data in the browser. Readings advance once per second. Map, measurement, history, forecast, and command examples can be reviewed without starting vehicle or database services.

## Start and stop

From PowerShell in the project folder:

```powershell
Set-Location apps/dashboard
npm.cmd run design:dev
```

Open **http://127.0.0.1:4175/preview.html**. The terminal prints the local address when Vite, the frontend development server, is ready. Keep that terminal open during review. Press **Ctrl+C** there to stop it. Reloading the page resets the sample session.

This uses the dashboard's existing Node.js 24 environment and locked dependencies. On a new checkout, run `npm.cmd ci` in `apps/dashboard` first. The preview binds to the local computer; it does not require credentials or a cloud account.

## Review the interface

| Tab | Interactive content |
|---|---|
| Overview | Four summaries, a selectable rolling trend, source state, age, coverage, and links to detail views |
| Fleet | Two sample vehicles, all 20 signals, vehicle selection, comparison bars, and reading details |
| Map | Pan, zoom, fit route, follow vehicle, observed trajectory, route, stops, station popups, and layer switches |
| Measurements | All 20 catalog signals, search, group/quality/freshness filters, metadata, and History links |
| History | Applied range and up to five signals, shared cursor, missing-data gaps, interval statistics, paginated readings, CSV and JSON downloads |
| Energy | Current and retained forecasts, projected charge and scenario range, reserve, station estimates, assumptions, conditional range/time, and model details |
| Activity | Event type and time filters, pagination, event detail, and browser connection events |
| System | Delivery path, rates, queue and storage accounting, rejection reasons, and unavailable instrumentation |
| Operator | Separate sample operator session, speed requests, replacement/cancellation, command stages, authority display, station intent, and command history |

Use **Preview state** in the upper right to select Running, Paused, Stale, Disconnected, Missing data, Invalid reading, or Out of domain. A pause freezes working time while reading age increases. Disconnection retains the last readings and blocks operator actions. Returning to Running restores updates and shows a reconnection notice. Missing data displays empty values. The invalid-reading example affects motor temperature. Out of domain makes the current energy forecast unavailable while retained forecasts remain inspectable.

**Open replay** opens a separate playhead with play/pause, seek, reset, speed, map, trend, and all recorded measurements. Its controls do not move the main dashboard clock. Escape closes the replay, and Tab stays within the dialog while it is open.

In Operator, select **Connect sample operator**, enter a speed cap and reason, and submit. Pending requests can be replaced or canceled. **Preview next stage** advances the sample through reservation, execution, and effect. Select a different **Preview outcome** to inspect no-effect or unknown-execution states. Records stay available while switching tabs; changing vehicle or reloading starts a new sample session.

## Data and presentation

The standard signals and units come directly from `catalog/monitoring-signals.json`. Summary cards convert charge to percent, speed to km/h, stored energy to kWh, and temperature to degrees Celsius. Detailed tables and history exports retain catalog units. The map and location readings share a coordinate fixture. Power equals voltage multiplied by current; stored energy uses a 300 kWh capacity. The fixtures illustrate interface behavior and do not form a vehicle physics model.

The map draws the reviewed local Montréal OpenStreetMap road subset. Road styling, labels, markers, and controls are rendered locally with Leaflet. Land-cover shapes and route/station examples are illustrative. Full cartography and route alignment will use reviewed geographic layers during live integration. No external tile requests occur. Contributor attribution remains on the map.

The shared interface uses **Working time**. Sample identity remains visible in the header and recording details. Fonts are bundled locally; [font provenance and license](../../apps/dashboard/src/ui/assets/README.md) accompany the source. Icons and the DriveTwin road symbol are original SVG artwork.

## Implementation boundary and checks

`preview.html` and `vite.preview.config.mjs` are separate entry points. `src/preview/` contains the fixture model, shared controls, charts, map, and tab components. The existing dashboard build and runtime interfaces retain their release scope. Browser-only sample commands and forecasts make no service calls.

Run the focused checks in `apps/dashboard`:

```powershell
npm.cmd test
npm.cmd run design:build
npm.cmd run format:check
```

The preview build writes ignored `dist-preview/` output, including the font license. Automated fixture checks cover catalog completeness, coordinate consistency, electrical summary relationships, missing-data statistics, and safe CSV cells. Browser review covers navigation, sample states, history downloads, command transitions, replay isolation, and responsive layout. Live source and command integration are the next implementation checkpoint after design review.
