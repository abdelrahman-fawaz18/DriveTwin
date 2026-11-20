# Dashboard visual direction

Design review, November 10, 2026. This specification records the approved visual direction and screen composition. Shared presentation components serve the connected application and the separate sample preview. Connected acceptance is tracked in the [verification record](../verification/dashboard-connected.md).

The current desktop presentation is accepted as the design baseline following review of the connected screenshots. This includes all nine tabs. The route-aware Energy Forecast update extends this baseline with recorded-route navigation, forecast outcomes, and selectable multi-signal History. Operator retains its existing behavior.

## Selected direction

The interface uses a light gray workspace, white analytical surfaces, and an asymmetric grid of rounded cards. Larger panels hold the main chart, map, or table. Smaller panels hold related readings and controls. Pink, violet, cyan, yellow-orange, and gray form the shared palette. Muted teal and olive-lime support secondary series and state summaries. Colored cards use subtle gradients, including the charcoal surfaces.

Charts share the interface palette and use clear axes, units, legends, and restrained grid lines. Gradient fills add depth below traces and inside selected bars. Values, lengths, and positions carry the quantitative information. Sidebar entries combine icons with text. A DriveTwin wordmark and a compact original symbol occupy the top left.

The selected references establish card placement, rounded corners, gradient treatment, integrated charts, and palette. The interactive preview establishes the implementation reference for palette, typography, spacing, and original logo artwork.

## Visual values

The hexadecimal values below approximate the supplied palette. A hexadecimal color is a six-digit description of its red, green, and blue components. Each gradient blends between two listed colors.

| Role | Colors | Application |
|---|---|---|
| Pink | `#ED3D8B` to `#B657AB` | Main accent, selected navigation, battery charge |
| Violet | `#9562C4` to `#6552B8` | Supporting series and selected analytical cards |
| Cyan-blue | `#34C4E8` to `#648BD8` | Motion and energy-related series |
| Yellow-orange | `#FFC62D` to `#F68057` | Thermal series and selected cards |
| Charcoal | `#434955` to `#252831` | Source-state and command-summary cards |
| Workspace and surfaces | `#F5F6F8`, `#FFFFFF`, `#E7E9EF` | Background, cards, and dividing lines |
| Text | `#252831`, `#616775` | Main text and supporting labels |

Inter is the bundled typeface. Values use aligned numerals so successive readings are easy to compare. Cards begin with a 20-pixel corner radius, smaller controls with 10 pixels, and card gaps with 20 pixels. The shared stylesheet defines these values. Fonts and icons follow the existing local-asset delivery approach.

Gradients concentrate on a few summary surfaces. Tables and detailed charts use light backgrounds. Text contrast is checked across the entire gradient, with dark text where cyan or yellow surfaces need it. Semantic states include a text label and a recognizable symbol. Pink and orange metric cards carry measurement identity. Warning and failure states have their own explicit labels and treatments.

Give related metric cards consistent typography and spacing. The charging recommendation uses dark gradients with white text and separates the cards with space. Maintain readable contrast across each gradient and avoid decorative colored top borders.

Expanded charging estimates follow the trip in three rows, using teal for charger arrival, olive for the charge transfer, and rose for the trip finish. The reserve crossing without the stop and the review deadline sit below the sequence. Section icons use white line art in a rounded tile, with distinct colors for trip progress, forecasts, history, route context, and storage.

## Shared layout

- A persistent sidebar contains Overview, Fleet, Map, Measurements, History, Energy Forecast, Activity, System, and Operator, in that order.
- The active tab has a softly tinted background, an accent icon, and a text label.
- The header identifies the selected vehicle, recording or run, sample or recording context, and browser connection. Fleet uses its own authorized vehicle selection.
- Source state, reading quality, reading age, and browser connection remain individually visible where relevant.
- Replay is a separate destination reached through a secondary sidebar link. Its playback identity, credentials, and controls retain their existing boundaries.
- Detailed metadata opens near its reading or record. Controls retain visible labels, keyboard access, focus indicators, and useful empty and unavailable states.
- On narrow screens the sidebar collapses, cards stack, and wide tables retain labeled horizontal scrolling.

## Screen composition

| Tab | Main visual | Supporting content and retained functions |
|---|---|---|
| Overview | One large rolling trend with a signal selector | Four summaries for charge, speed, stored energy, and motor temperature. Source state, working time, reading age, and coverage summary. Links to measurements, history, and route. |
| Fleet | Vehicle comparison bars and per-vehicle tables | Authorized vehicle selection, per-vehicle state, quality, units, age, and metadata. Optional metric comparison bars for multiple selected vehicles. All selected vehicles' registered signals remain accessible. |
| Map | Wide map with observed trajectory, planned route, and approved charging detours | Passenger stops, charging bays, current or last valid position, layer controls, freshness, route context, and map attribution. Gaps remain visible. |
| Measurements | Canonical grouped table of every registered signal | Search, component/quality/freshness filters, value, unit, age, observation and receipt times, source/cadence, metadata, and History links. |
| History | Any number of registered signal plots with a common cursor | Applied time-range query, individual unit axes, gaps, summaries, recorded values, pagination, and metadata-bearing CSV/JSON downloads. |
| Energy Forecast | Pink gradient route-energy card and fixed 0–100% charge comparison | Teal measured charge, blue lowest predicted charge, reddish arrival charge; reserve outcome, energy accounting, input provenance, saved-record navigation, and update trend. Current, below-reserve, infeasible, and stale states have distinct content. |
| Activity | Selectable event timeline and adjacent detail | Event type/time filters and pagination. Browser connection events have their own clearly labeled section. |
| System | Source-to-storage delivery overview | Existing queue, broker, ingestion, storage, receipt, rate, quality/rejection, and metric-age information. Missing instrumentation displays unavailable. Quantitative charts use available measurements. |
| Operator or Charging Advisor | Authorized vehicle controls or charging-decision review | The dashboard key grants the permissions configured for the run. The view distinguishes requested or approved actions from observed effects. |

Each detailed table has one primary home. Overview provides selected summaries and links. Fleet serves comparison across vehicles. History owns fixed-window time-series exploration. Energy owns model output and assumptions. Activity owns source events. Operator owns command outcomes. A chart may summarize the accompanying detail table, with identical units and scope.

The preview images illustrate visible screen areas. Scrolling, expansion, and detail views accommodate the complete retained content. A visual proposal does not establish that a backend metric or command is available in a particular deployment.

## Standard measurement coverage

The signal catalog defines the rows. The following twenty signals are the standard starting set. Extensions, including a registered coolant-inlet signal, appear through the same catalog-driven interface.

| Group | Signal | Stored unit |
|---|---|---|
| Navigation and motion | `location.latitude` | deg |
| Navigation and motion | `location.longitude` | deg |
| Navigation and motion | `motion.speed` | m/s |
| Navigation and motion | `motion.distance` | m |
| Navigation and motion | `motion.heading` | rad |
| Navigation and motion | `motion.acceleration` | m/s2 |
| Battery | `battery.pack.soc` | fraction |
| Battery | `battery.pack.voltage` | V |
| Battery | `battery.pack.current` | A |
| Battery | `battery.pack.power` | W |
| Battery | `battery.pack.energy` | J |
| Battery | `battery.pack.temperature` | K |
| Drivetrain | `traction.motor.speed` | rad/s |
| Drivetrain | `traction.motor.torque` | N.m |
| Drivetrain | `traction.motor.temperature` | K |
| Environment and loads | `ambient.temperature` | K |
| Environment and loads | `auxiliary.power` | W |
| Environment and loads | `hvac.power` | W |
| Position quality and braking | `location.horizontal_accuracy` | m |
| Position quality and braking | `braking.pressure` | Pa |

Summary cards retain explicit display conversions to percent, km/h, kWh, and degrees Celsius. Detailed values and exports preserve stored units. Chart labels identify the displayed unit. Missing values remain visibly unavailable, and gaps interrupt traces. Independent variables with different units use individual axes or panels. [Signal catalog](../../catalog/monitoring-signals.json)

## Interactive preview

The nine-tab browser preview implements this direction using local sample data. It includes original SVG icons and logo artwork, bundled Inter typography, local street geometry, charts, all standard signals, replay, and sample command flows. The shared time label is **Working time**. [Start and review the preview](../setup/dashboard-preview.md).

## Review artifacts

The local design gallery contains one generated example per tab and the exact generation prompts. The supplied references and generated review images remain in local review storage. The original road/twin-line logo and sidebar icons are shared SVG components.

Review proceeds from screen composition to an interactive browser preview using sample responses. The browser preview verifies typography, contrast, actual chart geometry, navigation, responsive layout, and fresh, stale, paused, disconnected, reconnecting, unavailable, and command-outcome states. Exact logo and icon consistency is established through shared components. Relevant interaction and live integration checks follow the approved implementation. [Dashboard workflow](../setup/dashboard.md)

## Connected presentation

The connected application uses the same rounded gradient cards, grouped measurement rows, compact header, and main-panel/sidebar composition as the preview. The map legend is contained by the map card; layer switches, position, and route context occupy its sidebar. The basemap is built from reviewed local OpenStreetMap streets, park/water polygons, and place names. Route geometry and vehicle positions come from scoped runtime APIs. Areas beyond the backdrop have no additional geographic detail. The position card uses exact UTC observation time, with working time secondary. Four rounded switches control observed trajectory, route, stops and stations, and labels.

System health shows seven observed delivery stages: Source, Sender, Broker, Ingestion, Database, API, and Browser. Their labels come from source registration, authenticated sender/receiver metrics, broker connection flags, a history query, the health response, and the browser update connection. Docker process state, per-container CPU, and per-container memory are not collected by this interface. Unavailable metrics remain unavailable.

Energy shows a forecast endpoint and scenario range when a forecast record is available. The monitoring review has no forecast producer; its empty state identifies that configuration. Operator status keeps request acceptance, durable reservation, execution, and independently measured effect separate. History retains fixed queries, gap handling, original readings, and downloads.

## Route-aware forecast navigation

Overview retains its signal selector, time-window selector, chart footer, and raw records alongside a compact current-forecast card. History opens empty from the sidebar and with relevant editable selections from a forecast. Each selected signal has its own chart, and the forecast cutoff is marked. Saved forecast links preserve the selected record across History and Map. Graph selection is uncapped; bounded sequential data requests preserve server safeguards.
