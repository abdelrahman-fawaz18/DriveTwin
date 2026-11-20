# Using the map, historical analysis, and operational views

This guide builds on [the first dashboard guide](dashboard.md). Acceptance results belong in [P1 status](../monitoring-status.md); the behaviors below describe the implemented design, not a performance or real-vehicle accuracy claim.

## Map coordinates and layers

**Latitude** measures north/south position; **longitude** measures east/west position. A Montreal position might be latitude 45.510 and longitude -73.573. Negative longitude means west of the reference meridian. Both values use degrees. **GeoJSON**, a standardized geographic JSON format, stores this pair as `[-73.573, 45.510]`: longitude first. Leaflet's native point arrays use the other order. Our explicit conversion boundary and known-location checks protect against swapping them.

A **geometry** describes a point, line, or area. Map **layers** group related geometries so you can turn them on/off independently. This map has local roads, the known planned route, a scheduled stop, hypothetical stations, observed trajectory segments and an observed-position marker. Leaflet draws the map and provides pan/zoom controls. It requests no external tiles or web fonts.

The trusted setup imports the same reviewed road network as the source. SUMO converts its local coordinates in meters to geographic degrees before any vehicle simulation steps. Only the assigned route/stop/station context, attribution and input fingerprints are published. A fingerprint identifies exact input bytes; it does not certify a real street or station. Station configuration now participates in source reproducibility fingerprints.

The planned route is the independently assigned hypothetical mission. Its lane geometry illustrates the assignment; actual lane choice is observed separately. The observed path uses already accepted readings. It never comes from a future simulation trajectory. Station positions and power limits are assumptions; road access and live availability are unverified. The map retains OpenStreetMap attribution and ODbL terms.

To use it:

1. In the local dashboard, select a vehicle, then choose **Map**.
2. Drag to pan, use the zoom buttons, and use **Fit map** to return to the reviewed area. The distance scale helps you judge meters/kilometers on screen. Camera zoom changes are immediate, so consecutive controls reach their requested views. Turning a layer on/off preserves your camera position.
3. **Center on vehicle** moves to the latest coherent observed position. **Follow vehicle** keeps the map centered as observations change. These are browser display actions.
4. Read the location's freshness, observation time and accuracy below the map. A healthy pause has its own state; an expired source or disconnected browser shows stale data.
5. Inspect the mission/stop/station details. A vehicle with no assigned map context receives an explicit unavailable state.

Latitude and longitude must be good readings from the same accepted observation. An invalid or incomplete current pair is unavailable. A historical last valid position can remain visible with that label. Missing/invalid coordinates, source-epoch changes and excessive time gaps break the observed line. The line connects sampled points within a valid segment; it does not prove the exact unsampled path between them. The first map follows the last 30 simulated minutes, with an explicit 5,000-point cap.

## Historical queries and shared cursors

A **historical query** selects data that is already stored. Its **snapshot revision** fixes which committed observations belong to the result. The result stays fixed as new data arrives; **Apply historical query** creates a new selection. A query made before the first reading remains empty until you apply a newer snapshot.

1. Choose **History**, or a measurement's **History** button.
2. Enter start/end values in simulation seconds. Use **Use latest time** to fill the latest observed end time, then **Apply historical query**. A query spans at most one simulated day.
3. Expand signal selection and choose up to five registered signals. Apply again. Each plot retains its own units and vertical scale; all plots share the selected time range.
4. Move the shared time slider using the mouse or arrow keys, or point at a plot. A nearby retained original reading is displayed. No retained point near the cursor does not itself prove a measurement was missing; use the complete raw export to inspect omitted readings. The cursor does not invent a value.
5. Expand a plot's recorded-values inspection for retained original point times and provenance. The complete raw download also includes missing/invalid samples and optional invalid raw values. Display formatting may round numbers; the export retains original numeric values.

**Aggregation** intentionally combines several readings into a statistic. **Display reduction** only reduces plotted points while retaining each segment's extrema. Neither changes stored observations. Interval statistics use explicit bucket widths and good readings only, with separate missing/invalid counts and min/mean/max. An empty bucket has no numeric value. Intervals are left-closed/right-open except that the final interval includes the query endpoint. The current database summary accepts at most 5,000 total signal intervals. The older compatible `/v1/summary` route retains its original 2,000-interval / 10,000-input bounds. The summary table displays up to 500 buckets and identifies a larger result explicitly.

## Exports and provenance

Choose **Download JSON** or **Download CSV** for the applied query. JSON (JavaScript Object Notation) is a structured format suitable for programs. CSV (comma-separated values) is a row/column text format commonly imported into analysis tools. Both exports preserve run/asset/mode, snapshot/range, policy fingerprint, signal definitions/units/model evidence, schema/catalog/recipe references, quality, UTC audit times and raw values. The current browser collects frozen raw export pages, up to 100,000 scalar readings / 64 MiB, and refuses incomplete or inconsistent downloads. The older compatible `/v1/export` route retains its 10,000-reading cap. See the [bounded history guide](bounded-history.md) for versions, validation and limits. Generated `evdt-*.json`/`evdt-*.csv` downloads and the `exports/` folder are ignored by Git and excluded from container build inputs.

CSV has one header, one `record_type=metadata` row with `metadata_json`, then `record_type=sample` rows. Keeping metadata as a standard row preserves an empty query's meaning. Filter on record type when analyzing samples. Spreadsheet formula-like text receives a leading apostrophe so it remains text; numeric values retain their numeric representation. JSON preserves unescaped structured text. An export is a read operation.

## Activity and system health

An **event** records something that happened, such as a source registration, declared state change, missing/invalid input, recovery or rejected packet. **Activity** shows bounded, snapshot-bound pages of safe event summaries. Use **Refresh activity** to include newer events; paging keeps the original revision and displays at most 500 events with an explicit limit notice. Event tracking begins with migration 004; migration 005 records forward quality recovery. Earlier source state changes are not reconstructed. Current status calculates heartbeat expiry from the recorded source state. Raw rejected bytes remain private. Browser connection events are held only in the current page and labeled separately.

**Metrics** are measurements about the software itself. **System** shows accepted-event counts, the last 30 seconds' average ingest rate, queue state, broker connections, rejection reasons, and Linux spool-volume usage. Small authenticated endpoints inside the isolated service network supply sender/receiver metrics. The read API checks their run/asset identity; the metric credential never enters the browser. These endpoints expose no vehicle commands and have no host-published port.

Unavailable metrics display unavailable. The queue for another vehicle is not reused as the selected vehicle's queue. The optional bounded-storage provider supplies explicit overflow accounting and has separate integration evidence. The normal release queue remains selected. Source clock-admission failures appear in rejection reasons; distributed clock accuracy remains unqualified. An unavailable metric is returned with its status and reason.

## Reproduce verification

Run the verification commands in Windows PowerShell from `<checkout>`.

```powershell
python scripts/dev.py check
python scripts/monitoring/qualify_dashboard.py --evidence-dir evidence/p1/YOUR_NEW_C2_RUN
```

The first command uses pinned tools to check source, original/current contracts, TypeScript, frontend build and isolated tests. The second owns the real services, checks map/query/export/metrics boundaries and drives Edge/Chromium. Its first browser pass uses actual stepped-source data. A separate controlled producer then exercises explicit zero/missing/invalid/recovery, pause, heartbeat loss, resume and finish through actual registration, MQTT, persistence and the UI. That producer is test equipment, not a qualified second vehicle model. Tests inspect the actual saved data as well as screenshots. Raw results, credentials and screenshots stay ignored; sanitized summaries are reviewed separately.

Do not edit source during qualification: changing inputs invalidates the result. The controller releases waiting fixtures on failures and removes only its owned containers/volumes. Temporary test services stop after verification. Replay, application restore, full workload/retention/physical capacity and vehicle-control gates retain their separate phases.

See [ADR-0014](../adr/0014-map-history-and-operational-views.md) for design decisions.

## Extending the implementation

- Add a supported scalar through a versioned catalog, producer mapping and trusted ownership/timing policy. The generic measurement/history controls read that metadata. An optional overview card belongs in `apps/dashboard/src/features/overview/overview.ts`; it does not define the shared signal set.
- Change assigned route/stops in `simulation/montreal/mission.json` and hypothetical stations in `simulation/montreal/stations.json`. Regenerate context and policy under a new run; existing context/policy bytes are immutable. Source and context share the reviewed importer in `src/evdt/monitoring/network.py`.
- Coordinate pairing and gap rules live in `mapModel.ts` and `analysis_views.py`. A future physical navigation adapter must supply a coherent position observation; unrelated latest latitude/longitude values are insufficient.
- Add diagnostics through a scoped internal metrics provider and the read API. Keep service credentials on the backend and expose unavailable/expired data explicitly. Real availability, quota enforcement and dropped-record instrumentation need their own measured acceptance.

The normal qualification command automates context generation, migrations and private credentials. Its temporary deployments are intentionally removed; a persistent local developer/demo launcher remains separate operational work.
