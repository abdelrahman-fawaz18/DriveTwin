import { useEffect, useMemo, useState } from "react";
import * as L from "leaflet";
import type { DemoCase, Player, Frame } from "./demoModel.ts";
import { projectCase, recordedActivity } from "./demoModel.ts";
import {
  recordedSnapshot,
  recordedForecasts,
  historyPoints,
} from "./demoAdapter.ts";
import { Icon } from "../../ui/Icon.tsx";
import { SectionHeading } from "../../ui/SectionHeading.tsx";
import { Chart } from "../../ui/Chart.tsx";
import { FleetVehicle } from "../fleet/FleetVehicle.tsx";
import { SummaryMetrics } from "../overview/SummaryMetrics.tsx";
import { SignalPicker } from "../history/SignalPicker.tsx";
import { ObservedChargingStop } from "../charging/ObservedChargingStop.tsx";
import { MapLayers, MapLegend } from "../map/MapLayers.tsx";
import { useMapScene } from "../map/useMapScene.ts";
import { chargingMarkers, type Fix } from "../map/mapModel.ts";
import {
  measurementName,
  signalColor,
  type Tab,
} from "../../app/presentation.ts";
import {
  ForecastComparisons,
  chargeValues,
  percent,
} from "../forecast/RouteEnergyDetails.tsx";
import { ForecastOverview } from "../forecast/ForecastOverview.tsx";
import { ForecastChargeTrend } from "../forecast/ForecastChargeTrend.tsx";
import { RecordedForecastSummary } from "../forecast/RecordedForecastSummary.tsx";
import type { RouteEnergyRead } from "../forecast/useRouteEnergy.ts";

export const num = (v: number | null | undefined, digits = 2) =>
  v == null
    ? "—"
    : v.toLocaleString("en-US", { maximumFractionDigits: digits });
export const workingDuration = (v: number | null | undefined) =>
  v == null
    ? "Unavailable"
    : `${Math.floor(Math.round(Math.max(0, v)) / 60)} min ${Math.round(Math.max(0, v)) % 60} s`;
export type View = ReturnType<typeof projectCase>;
export function arrivalLabel(view: View, t: number) {
  const f = view.forecast;
  return f?.summary && f.cutoff + f.summary.duration >= t
    ? workingDuration(f.cutoff + f.summary.duration - t) + " working time"
    : null;
}

export function RecordedMap({
  data,
  view,
  compact = false,
}: {
  data: DemoCase;
  view: View;
  compact?: boolean;
}) {
  const [layers, setLayers] = useState([
      "roads",
      "labels",
      "route",
      "trajectory",
      "endpoints",
      "stops",
      "stations",
      "detour",
    ]),
    [follow, setFollow] = useState(compact);
  const initialView = useMemo(() => {
    if (!compact) return undefined;
    const departure = data.map.features.find(
      (f) => f.properties.id === "departure" && f.geometry.type === "Point",
    );
    return departure?.geometry.type === "Point"
      ? {
          coordinates: departure.geometry.coordinates as [number, number],
          zoom: 15,
        }
      : undefined;
  }, [compact, data.map]);
  const lat = data.catalog.findIndex((s) => s.id === "location.latitude"),
    lon = data.catalog.findIndex((s) => s.id === "location.longitude");
  const points = useMemo<Fix[]>(
    () =>
      view.frames.map((f) => ({
        event_id: `observation-${f[0]}`,
        sim_time_s: f[0],
        observed_at: "",
        source_id: "recorded",
        source_epoch: 1,
        coordinates:
          f[1][lon] != null &&
          f[1][lat] != null &&
          (f[2]?.[lat] ?? "good") === "good" &&
          (f[2]?.[lon] ?? "good") === "good"
            ? [f[1][lon]!, f[1][lat]!]
            : null,
        quality: "good",
        reason: null,
      })),
    [view.frames, lat, lon],
  );
  const coordinates = points.at(-1)?.coordinates,
    approved = view.events.some((e) => e.kind === "approval");
  const markers = useMemo(
    () =>
      chargingMarkers(
        data.map,
        approved ? { approved: data.manifest.decision.stationId } : {},
      ),
    [data, approved],
  );
  const charging = ["charging", "target_reached"].includes(
    view.transfer?.state ?? "",
  );
  const { host, map } = useMapScene(
    data.map,
    points,
    2,
    coordinates ? { coordinates, freshness: "recorded" } : null,
    layers,
    follow,
    markers,
    charging,
    initialView,
  );
  useEffect(() => {
    const own = map.current;
    if (!own || !compact) return;
    const stopFollowing = () => setFollow(false);
    own.on("dragstart", stopFollowing);
    return () => {
      own.off("dragstart", stopFollowing);
    };
  }, [map, compact, data.map]);
  function fit() {
    if (compact) setFollow(false);
    const bounds = L.geoJSON(
      data.map.features.filter((f) => f.properties.layer === "route"),
    ).getBounds();
    if (bounds.isValid())
      map.current?.fitBounds(bounds, { padding: [35, 35], maxZoom: 16 });
  }
  const scene = (
    <section className={`map-card ${compact ? "recorded-overview-map" : ""}`}>
      <div
        ref={host}
        className="vehicle-map"
        role="region"
        aria-label="Vehicle map with planned route and observed trajectory"
      />
      <div className="map-tools">
        <button onClick={fit} title="Fit map" aria-label="Fit map">
          <Icon name="locate" />
        </button>
        <button
          disabled={!coordinates}
          title={
            compact
              ? follow
                ? "Stop following vehicle"
                : "Follow vehicle"
              : "Center on vehicle"
          }
          aria-label={
            compact
              ? follow
                ? "Stop following vehicle"
                : "Follow vehicle"
              : "Center on vehicle"
          }
          aria-pressed={compact ? follow : undefined}
          onClick={() => {
            if (compact) {
              setFollow(!follow);
              if (!follow && coordinates)
                map.current?.setView([coordinates[1], coordinates[0]], 15);
            } else if (coordinates)
              map.current?.setView([coordinates[1], coordinates[0]], 17);
          }}
        >
          <Icon name="Map" />
        </button>
      </div>
      <MapLegend
        items={[
          { tone: "observed", label: "Observed trajectory" },
          { tone: "planned", label: "Planned route" },
          { tone: "stop", label: "Passenger stop" },
          { tone: "departure", label: "Departure" },
          { tone: "destination", label: "Destination" },
          { tone: "charge", label: "Charging bay", icon: "ϟ" },
          ...(approved
            ? [{ tone: "approved", label: "Approved charging bay", icon: "ϟ" }]
            : []),
        ]}
      />
    </section>
  );
  if (compact) return <div className="recorded-map-preview">{scene}</div>;
  return (
    <div className="map-layout connected-map">
      {scene}
      <aside className="stack">
        <section className="panel position-card gradient cyan">
          <Icon name="Map" />
          <h2>Observed position</h2>
          <strong>
            {coordinates
              ? `${Math.abs(coordinates[1]).toFixed(5)}° ${coordinates[1] < 0 ? "S" : "N"}`
              : "Unavailable"}
            <br />
            {coordinates
              ? `${Math.abs(coordinates[0]).toFixed(5)}° ${coordinates[0] < 0 ? "W" : "E"}`
              : ""}
          </strong>
          <p>Recorded observation</p>
          <small>
            {points.length
              ? `Observed at ${num(points.at(-1)!.sim_time_s)} working seconds`
              : "No recorded position yet"}
          </small>
        </section>
        <section className="panel">
          <h2>Map layers</h2>
          <MapLayers
            layers={layers}
            setLayers={setLayers}
            items={[
              { id: "trajectory", label: "Observed trajectory" },
              {
                id: "route",
                label: "Planned route",
                related: ["endpoints", "detour"],
              },
              {
                id: "stops",
                label: "Stops and charging bays",
                related: ["stations"],
              },
              { id: "labels", label: "Map labels" },
            ]}
          >
            <button aria-pressed={follow} onClick={() => setFollow((v) => !v)}>
              {follow ? "Following vehicle" : "Follow vehicle"}
            </button>
          </MapLayers>
        </section>
        <p className="recorded-map-caption">
          {
            data.map.features.filter((f) => f.properties.layer === "stops")
              .length
          }{" "}
          passenger stops
          {view.progress?.remainingM != null && (
            <> · {num(view.progress.remainingM / 1000)} km remaining</>
          )}
        </p>
      </aside>
    </div>
  );
}

export function RecordedTrend({
  data,
  frames,
  id,
  from,
  to,
  cursor,
  seek,
  panel = true,
  compact = false,
}: {
  data: DemoCase;
  frames: Frame[];
  id: string;
  from: number;
  to: number;
  cursor: number;
  seek?: (t: number) => void;
  panel?: boolean;
  compact?: boolean;
}) {
  const [inspect, setInspect] = useState(false),
    signal = data.catalog.find((s) => s.id === id)!;
  const points = historyPoints(data, frames, id, from, to);
  const percentage = id === "battery.pack.soc" && signal.unit === "1";
  const content = (
    <>
      <Chart
        label={`${measurementName(id)} recorded history`}
        points={
          percentage
            ? points.map((p) => ({
                ...p,
                value: p.value == null ? null : p.value * 100,
              }))
            : points
        }
        color={signalColor(id)}
        unit={percentage ? "%" : signal.unit}
        height={compact ? 125 : 215}
        width={compact ? 1200 : 800}
        tickPrecision={percentage ? 2 : undefined}
        cursor={cursor}
        onSeek={seek}
      />
      <div className="chart-foot recorded-chart-foot">
        <div className="recorded-reading-summary">
          <span>
            <Icon name="layers" size={15} />
            <strong>{points.filter((p) => p.value !== null).length}</strong>
            recorded readings
          </span>
          <small>Gaps preserved · extrema retained</small>
        </div>
        <span className="recorded-chart-time">
          <Icon name="clock" size={15} />
          <span>
            Recording as of <strong>{num(to)} working s</strong>
          </span>
        </span>
      </div>
      <details
        className="raw recorded-chart-audit"
        open={inspect}
        onToggle={(e) => setInspect(e.currentTarget.open)}
      >
        <summary>Inspect recorded values and provenance</summary>
        <p className="fine">
          {signal.provenance} · stored unit {signal.unit}
        </p>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Working time</th>
                <th>Stored value</th>
              </tr>
            </thead>
            <tbody>
              {points.slice(-20).map((p, i) => (
                <tr key={i}>
                  <td>{num(p.t)} s</td>
                  <td>
                    {num(p.value, 5)} {signal.unit}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
  return panel ? (
    <section className="panel">
      <div className="panel-head">
        <h2>{measurementName(id)}</h2>
        <span className="badge">Recorded</span>
      </div>
      {content}
    </section>
  ) : (
    content
  );
}

export function RecordedOverview({
  data,
  player,
  view,
  navigate,
}: {
  data: DemoCase;
  player: Player;
  view: View;
  navigate: (tab: Tab) => void;
}) {
  const [signal, setSignal] = useState("battery.pack.soc"),
    [windowS, setWindowS] = useState(60);
  const snapshot = recordedSnapshot(data, player, view),
    latest = recordedForecasts(data, player, view).at(-1),
    charging = ["charging", "target_reached"].includes(
      view.transfer?.state ?? "",
    );
  const forecast: RouteEnergyRead = {
    current:
      charging || latest?.payload.status !== "available"
        ? null
        : latest.payload,
    diagnostic:
      charging || latest?.payload.status !== "infeasible"
        ? null
        : latest.payload,
    data: null,
    fresh: true,
    error: "",
    unsupported: false,
    last: 0,
    now: 0,
  };
  const distance = snapshot.items.find((s) => s.signal_id === "motion.distance")
    ?.observation?.sample.value;
  return (
    <div className="recorded-overview">
      <SummaryMetrics snap={snapshot} connected recorded compact />
      <div className="overview-grid recorded-overview-grid">
        <section
          className="panel recorded-route-panel"
          aria-label="Trip progress"
        >
          <div className="panel-head">
            <div>
              <h2>{data.manifest.title}</h2>
              <p className="fine recorded-route-distance">
                {distance == null
                  ? "Awaiting distance"
                  : `${num(distance / 1000)} km traveled`}
                <span aria-hidden="true"> · </span>
                {view.progress?.remainingM == null
                  ? "Remaining distance unavailable"
                  : `${num(view.progress.remainingM / 1000)} km remaining`}
              </p>
            </div>
            <button className="secondary" onClick={() => navigate("Map")}>
              <Icon name="arrow" size={16} />
              Open map
            </button>
          </div>
          <RecordedMap data={data} view={view} compact />
        </section>
        {charging ? (
          <section className="panel forecast-overview compact-forecast recorded-overview-charging">
            <RecordedCharging
              data={data}
              view={view}
              arrival={arrivalLabel(view, player.t)}
              open={() => navigate("Operator")}
              compact
            />
          </section>
        ) : (
          <ForecastOverview
            forecast={forecast}
            connected
            compact
            reserve={data.manifest.reserveSoc}
            open={() => navigate("Energy")}
          />
        )}
      </div>
      <section className="panel trend-panel recorded-overview-trend">
        <div className="panel-head">
          <h2>{measurementName(signal)}</h2>
          <div className="trend-controls">
            <label>
              Signal
              <select
                aria-label="Trend signal"
                value={signal}
                onChange={(e) => setSignal(e.target.value)}
              >
                {data.catalog.map((s) => (
                  <option key={s.id} value={s.id}>
                    {measurementName(s.id)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Window
              <select
                aria-label="History window"
                value={windowS}
                onChange={(e) => setWindowS(Number(e.target.value))}
              >
                <option value={60}>Last 60 s</option>
                <option value={300}>Last 5 min</option>
                <option value={14400}>Full trip</option>
              </select>
            </label>
          </div>
        </div>
        <RecordedTrend
          data={data}
          frames={view.frames}
          id={signal}
          from={Math.max(0, player.t - windowS)}
          to={player.t}
          cursor={player.t}
          panel={false}
          compact
        />
      </section>
    </div>
  );
}

export function RecordedEnergy({
  data,
  player,
  view,
  navigate,
  history,
}: {
  data: DemoCase;
  player: Player;
  view: View;
  navigate: (tab: Tab) => void;
  history: (from?: number, to?: number) => void;
}) {
  const [selected, setSelected] = useState("current"),
    [section, setSection] = useState("saved");
  const records = useMemo(
      () => recordedForecasts(data, player, view),
      [data, view],
    ),
    record =
      selected === "current"
        ? records.at(-1)
        : records.find((r) => String(r.fence) === selected),
    current = record?.payload;
  const charging =
    selected === "current" &&
    ["charging", "target_reached"].includes(view.transfer?.state ?? "");
  return (
    <div className="energy-panel route-energy">
      <section
        className="recorded-energy-header"
        aria-label="Forecast selection"
      >
        <div className="recorded-energy-status-row">
          <span
            role="status"
            className={`recorded-forecast-status ${current?.summary?.ledger.reserve_breached || current?.status === "infeasible" ? "warning" : ""}`}
          >
            <Icon
              name={
                charging
                  ? "battery"
                  : current?.summary?.ledger.reserve_breached
                    ? "alert"
                    : "Energy"
              }
              size={15}
            />
            {charging
              ? "Driving forecast paused during measured charge transfer"
              : current?.status === "available"
                ? current.summary?.ledger.reserve_breached
                  ? `Predicted below the ${num(data.manifest.reserveSoc * 100)}% reserve`
                  : "Predicted above reserve"
                : current
                  ? `Forecast ${current.status}`
                  : "Waiting for the first forecast"}
          </span>
          {current && (
            <span className="fine">
              Readings at {num(current.cutoff_working_s)} s
            </span>
          )}
          <div className="recorded-forecast-controls">
            <label className="sr-only" htmlFor="recorded-forecast">
              Forecast
            </label>
            <select
              id="recorded-forecast"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
            >
              <option value="current">Current forecast</option>
              {records.map((r) => (
                <option key={r.fence} value={r.fence}>
                  {num(r.payload.cutoff_working_s)} working seconds · #{r.fence}
                </option>
              ))}
            </select>
          </div>
        </div>
        <details className="recorded-details recorded-forecast-details">
          <summary>Forecast details</summary>
          <dl className="recorded-system-facts">
            <div>
              <dt>Forecast cutoff</dt>
              <dd>
                {current
                  ? `${num(current.cutoff_working_s)} working s`
                  : "Not recorded yet"}
              </dd>
            </div>
            <div>
              <dt title="First recorded observation of the published result; it may have been published earlier">
                Available by
              </dt>
              <dd>
                {num(
                  view.forecasts.find(
                    (f) => f.inputId === current?.input_sha256,
                  )?.available,
                )}{" "}
                working s
              </dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{current?.model_version ?? "Not recorded yet"}</dd>
            </div>
            <div>
              <dt>Update reason</dt>
              <dd>
                {record?.trigger?.replaceAll("_", " ") ?? "Not recorded yet"}
              </dd>
            </div>
            <div>
              <dt>Input reference</dt>
              <dd className="recorded-hash">
                {current?.input_sha256 ?? "Not recorded yet"}
              </dd>
            </div>
          </dl>
          {current && (
            <button
              onClick={() =>
                history(
                  Math.max(0, current.cutoff_working_s - 60),
                  current.cutoff_working_s,
                )
              }
            >
              Inspect battery readings <Icon name="arrow" size={15} />
            </button>
          )}
        </details>
      </section>
      {charging ? (
        <RecordedCharging
          data={data}
          view={view}
          arrival={arrivalLabel(view, player.t)}
          open={() => navigate("Operator")}
        />
      ) : current ? (
        <RecordedForecastSummary
          forecast={current}
          reserve={data.manifest.reserveSoc}
          historical={selected !== "current"}
          records={records}
          openMap={() => navigate("Map")}
        />
      ) : (
        <section className="panel">
          <p>No forecast had been published at this point in the recording.</p>
        </section>
      )}
      <section className="panel route-energy-history">
        <div
          className="route-energy-tabs"
          role="tablist"
          aria-label="Forecast history"
        >
          {[
            ["saved", "Saved forecasts"],
            ["comparison", "Forecast vs. observed"],
            ["original", "Recorded model records"],
          ].map(([id, label]) => (
            <button
              key={id}
              role="tab"
              aria-selected={section === id}
              className={section === id ? "active" : ""}
              onClick={() => setSection(id!)}
            >
              {label}
            </button>
          ))}
        </div>
        {section === "saved" ? (
          <div className="table-scroll">
            <table className="route-energy-saved-table">
              <thead>
                <tr>
                  <th>Cutoff</th>
                  <th>Update reason</th>
                  <th className="charge-measured">Measured at forecast</th>
                  <th className="charge-lowest">Lowest predicted</th>
                  <th className="charge-arrival">Predicted arrival</th>
                  <th>Net energy</th>
                  <th>Inspect</th>
                </tr>
              </thead>
              <tbody>
                {records.map((r) => {
                  const s = r.payload.summary,
                    c = chargeValues(s);
                  return (
                    <tr key={r.fence}>
                      <td>
                        <button
                          className="route-energy-record"
                          onClick={() => setSelected(String(r.fence))}
                        >
                          {num(r.payload.cutoff_working_s)} s
                        </button>
                      </td>
                      <td>
                        {r.trigger === "first_ready"
                          ? "Initial forecast"
                          : r.trigger?.replaceAll("_", " ")}
                      </td>
                      {(["measured", "lowest", "arrival"] as const).map((k) => (
                        <td
                          key={k}
                          className={`charge-${k} route-energy-number`}
                        >
                          {percent(c[k])}
                        </td>
                      ))}
                      <td>
                        {s
                          ? `${num(s.ledger.net_battery_j / 3600000)} kWh`
                          : "—"}
                      </td>
                      <td>
                        <button
                          className="text-button"
                          onClick={() => setSelected(String(r.fence))}
                          aria-label={`Inspect forecast at ${num(r.payload.cutoff_working_s)} working seconds`}
                        >
                          Inspect <Icon name="arrow" size={14} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : section === "comparison" ? (
          <ForecastComparisons
            data={{
              items: records,
              comparisons: view.comparisons.map((c, i) => ({
                fence: i + 1,
                created_at: "",
                payload: {
                  scope: {
                    run_id: "browser-demo",
                    asset_id: "bus-001",
                    source_id: "recorded",
                    source_epoch: 1,
                    session_id: "recorded",
                  },
                  input_sha256: `comparison-${i + 1}`,
                  from_working_s: c.from,
                  until_working_s: c.until,
                  predicted_net_j: c.predicted,
                  observed_net_j: c.observed,
                  signed_error_j: c.error,
                },
              })),
            }}
            openHistory={(entry) => history(entry.from, entry.to)}
          />
        ) : (
          <details open>
            <summary>Recorded forecast provenance</summary>
            <pre className="recorded-json">
              {JSON.stringify(view.forecasts, null, 2)}
            </pre>
          </details>
        )}
      </section>
      {charging &&
        records.filter((r) => r.payload.status === "available").length > 1 && (
          <ForecastChargeTrend
            records={records}
            reserve={data.manifest.reserveSoc}
          />
        )}
    </div>
  );
}

export function RecordedCharging({
  data,
  view,
  arrival,
  open,
  compact = false,
}: {
  data: DemoCase;
  view: View;
  arrival?: string | null;
  open?: () => void;
  compact?: boolean;
}) {
  const e = view.transfer;
  if (!e) return null;
  return (
    <ObservedChargingStop
      effect={{
        state: e.state as "charging",
        bay_id: data.manifest.decision.stationId,
        delivered_j: e.deliveredKwh * 3600000,
        observed_working_s: e.t,
      }}
      bayName={() => data.manifest.decision.stationName}
      arrival={arrival}
      openAdvisor={open}
      forecastNote={
        compact
          ? "Driving forecast paused during charging"
          : open
            ? "Driving forecasts resume after recorded departure."
            : undefined
      }
    />
  );
}

export function ChargingOffer({
  data,
  t,
  openMap,
}: {
  data: DemoCase;
  t: number;
  openMap: () => void;
}) {
  const d = data.manifest.decision;
  return (
    <div className="charge-recommendation recorded-charge-offer">
      <div className="charge-offer-title">
        <div>
          <h3>{d.stationName}</h3>
        </div>
        <button className="secondary" onClick={openMap}>
          <Icon name="Map" size={16} /> Show on map
        </button>
      </div>
      <p>Along the planned route · Available in the recording</p>
      <dl className="charge-summary">
        <div className="charge-target">
          <dt>
            <Icon name="battery" size={15} /> Charge to
          </dt>
          <dd>{percent(d.targetSoc)}</dd>
        </div>
        <div className="charge-duration">
          <dt>
            <Icon name="clock" size={15} /> Charging duration
          </dt>
          <dd>{workingDuration(d.chargingSeconds)}</dd>
        </div>
        <div className="charge-minimum">
          <dt>
            <Icon name="shield" size={15} /> Lowest with this stop
          </dt>
          <dd>{percent(d.minimumSocWithStop)}</dd>
        </div>
      </dl>
      <p className="charge-offer-arrival">
        <span>Estimated arrival with this stop</span>
        <strong>
          {workingDuration(d.arrivalWorkingS - t)} <small>working time</small>
        </strong>
      </p>
    </div>
  );
}

export function RecordedAdvisor({
  data,
  player,
  view,
  review,
  openMap,
}: {
  data: DemoCase;
  player: Player;
  view: View;
  review: () => void;
  openMap: () => void;
}) {
  return (
    <section
      className="panel operator-panel charging-advisor"
      aria-label="Charging Advisor"
    >
      <header className="charge-heading">
        <SectionHeading title="Decision status" icon="Energy" tone="teal" />
        <span className="charge-mode">Recorded decisions</span>
      </header>
      <div className="charge-decision-state">
        <strong>Your decision</strong>
        <p>
          {player.phase === "decision"
            ? "Playback paused · ready for your decision"
            : player.choice === "charge"
              ? (view.events.at(-1)?.title ??
                "Charging approved · awaiting recorded handoff")
              : player.choice === "continue"
                ? "Continuing without a charging stop"
                : "Advice becomes available at the decision checkpoint"}
        </p>
      </div>
      {player.phase === "decision" && (
        <>
          <ChargingOffer data={data} t={player.t} openMap={openMap} />
          <button className="primary" onClick={review}>
            Review charging stop
          </button>
        </>
      )}
      <RecordedCharging
        data={data}
        view={view}
        arrival={arrivalLabel(view, player.t)}
      />
      {player.choice && (
        <section className="charge-outcomes">
          <h3>Charging decision timeline</h3>
          <RecordedEvents events={view.events} seek={undefined} />
        </section>
      )}
      {!view.events.length && player.phase !== "decision" && (
        <p className="muted">
          Recorded approvals and independently observed effects appear as
          playback advances.
        </p>
      )}
    </section>
  );
}

export function RecordedEvents({
  events,
  seek,
}: {
  events: { t: number; title: string; kind: string }[];
  seek?: (t: number) => void;
}) {
  return events.length ? (
    <ol className="trip-timeline">
      {events.map((e, i) => (
        <li key={i}>
          <strong>{e.title}</strong>
          {seek ? (
            <button className="text-button" onClick={() => seek(e.t)}>
              {num(e.t)} working s
            </button>
          ) : (
            <span>{num(e.t)} working s</span>
          )}
        </li>
      ))}
    </ol>
  ) : (
    <p className="empty">
      No events recorded before the current playback position.
    </p>
  );
}

export function RecordedFleet({
  data,
  player,
  view,
  navigate,
}: {
  data: DemoCase;
  player: Player;
  view: View;
  navigate: (tab: Tab) => void;
}) {
  const snap = recordedSnapshot(data, player, view);
  return (
    <div className="fleet recorded-fleet">
      <section className="panel recorded-fleet-scope">
        <span className="recorded-scope-icon">
          <Icon name="Fleet" />
        </span>
        <div>
          <h2>One recorded vehicle</h2>
          <p className="fine">{data.manifest.title}</p>
        </div>
        <span className="badge">bus-001</span>
        <button onClick={() => navigate("Map")}>
          <Icon name="Map" size={16} /> View route
        </button>
      </section>
      <FleetVehicle
        recorded
        asset="bus-001"
        state={{ snap, connection: "live" }}
        open={() => navigate("Overview")}
      />
      <details className="recorded-details">
        <summary>Recording coverage and units</summary>
        <p>
          {view.current ? data.catalog.length : 0} of {data.catalog.length}{" "}
          registered signals observed at this playback position. Values retain
          their original stored units and quality; the vehicle panel converts
          supported units for display.
        </p>
        <button onClick={() => navigate("Measurements")}>
          Inspect measurements <Icon name="arrow" size={16} />
        </button>
      </details>
    </div>
  );
}

export { SignalPicker };

export function RecordedActivity({
  view,
  seek,
}: {
  view: View;
  seek?: (t: number) => void;
}) {
  const [kind, setKind] = useState("all"),
    [selected, setSelected] = useState<number | null>(null);
  const activity = recordedActivity(view.events, kind, selected),
    event = activity.selected?.event;
  return (
    <>
      <section className="panel">
        <div className="panel-head">
          <SectionHeading
            eyebrow="RECORDED TRIP EVENTS"
            title="Recorded activity"
            icon="Activity"
            tone="indigo"
          />
          <span className="fine">Through the playback position</span>
        </div>
        <div className="filters">
          <label>
            Event type
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="all">All event types</option>
              {[...new Set(view.events.map((e) => e.kind))].map((k) => (
                <option key={k} value={k}>
                  {k.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <button className="secondary" onClick={() => setKind("all")}>
            Clear filters
          </button>
        </div>
        <p className="query-note">
          {activity.entries.length} of {view.events.length} recorded trip events
          match.
        </p>
        <div className="activity-grid">
          <ol className="event-timeline">
            {activity.entries.map(({ event: e, id }) => (
              <li key={id}>
                <button
                  className={`event-row ${activity.selected?.id === id ? "selected" : ""}`}
                  onClick={() => setSelected(id)}
                >
                  <span
                    className={`event-symbol ${e.kind === "reserve" ? "amber" : "cyan"}`}
                  >
                    <Icon name={e.kind === "reserve" ? "alert" : "Activity"} />
                  </span>
                  <span>
                    <strong>{e.title}</strong>
                    <small>{num(e.t)} working s</small>
                  </span>
                  <span className="event-arrow">›</span>
                </button>
              </li>
            ))}
          </ol>
          <aside className="stack">
            <section className="panel">
              <SectionHeading
                title="Event details"
                icon="Activity"
                tone="teal"
              />
              {event ? (
                <>
                  <h3>{event.title}</h3>
                  <dl className="key-values">
                    <dt>Event</dt>
                    <dd>{event.kind.replaceAll("_", " ")}</dd>
                    <dt>Working time</dt>
                    <dd>{num(event.t)} s</dd>
                    <dt>Source</dt>
                    <dd>Selected recording</dd>
                  </dl>
                  {seek && (
                    <button onClick={() => seek(event.t)}>
                      Inspect recorded moment
                    </button>
                  )}
                </>
              ) : (
                <p className="empty">
                  No event has occurred at this playback position.
                </p>
              )}
            </section>
          </aside>
        </div>
      </section>
    </>
  );
}

export function RecordedSystem({ data, view }: { data: DemoCase; view: View }) {
  const included = [
    ["Vehicle readings", `${data.catalog.length} signals`],
    ["Route & passenger stops", "Included"],
    ["Energy forecasts", "Included"],
    ["Charging decisions & effects", "Included"],
  ];
  return (
    <div className="recorded-system">
      <div className="recorded-info-note">
        <Icon name="monitor" />
        This is recorded playback. Live server, queue, and connection metrics
        were not captured.
      </div>
      <div className="recorded-system-grid">
        <section className="panel" aria-label="System status">
          <div className="panel-head">
            <h2>Recording summary</h2>
            <span className="badge good">
              <Icon name="shield" size={13} />
              Integrity verified
            </span>
          </div>
          <dl className="recorded-system-stats">
            <div>
              <dt>Working time reached</dt>
              <dd>{num(view.frames.at(-1)?.[0] ?? 0)} s</dd>
            </div>
            <div>
              <dt>Registered signals</dt>
              <dd>{data.catalog.length}</dd>
            </div>
            <div>
              <dt>Forecasts reached</dt>
              <dd>{view.forecasts.length}</dd>
            </div>
          </dl>
          <dl className="recorded-system-facts">
            <div>
              <dt>Vehicle</dt>
              <dd>bus-001</dd>
            </div>
            <div>
              <dt>Demo format</dt>
              <dd>{data.manifest.version}</dd>
            </div>
            <div>
              <dt>Trip</dt>
              <dd>{data.manifest.title}</dd>
            </div>
            <div>
              <dt>Control</dt>
              <dd>Recorded decisions only</dd>
            </div>
          </dl>
        </section>
        <section className="panel">
          <h2>Included in this recording</h2>
          <dl className="recorded-included">
            {included.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>
                  <Icon name="check" size={14} />
                  {value}
                </dd>
              </div>
            ))}
          </dl>
          <p className="fine">
            Raw values and provenance are available in Measurements and History.
          </p>
        </section>
      </div>
      <details className="panel recorded-architecture">
        <summary>
          <strong>How the live digital twin connects</strong>
          <span>Architecture reference</span>
        </summary>
        <p className="fine">
          System structure only — these are not live health indicators.
        </p>
        <div className="delivery-path">
          {[
            ["Vehicle", "Fleet"],
            ["Sender", "send"],
            ["Broker", "layers"],
            ["Ingestion", "download"],
            ["Database", "storage"],
            ["API", "code"],
            ["Browser", "monitor"],
          ].map(([name, icon], i) => (
            <div className="delivery-node" key={name}>
              <span className="node-icon">
                <Icon name={icon!} size={24} />
              </span>
              <strong>{name}</strong>
              {i < 6 && <span className="node-arrow">→</span>}
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
