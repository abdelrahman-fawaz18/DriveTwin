import { useState } from "react";
import type { Snapshot } from "../../monitoring/model.ts";
import { label, value } from "../../monitoring/model.ts";
import { SummaryMetrics } from "./SummaryMetrics.tsx";
import { Trend } from "../../ui/Trend.tsx";
import { Icon } from "../../ui/Icon.tsx";
import { SectionHeading } from "../../ui/SectionHeading.tsx";
import { measurementName, type Tab } from "../../app/presentation.ts";
import { ForecastOverview } from "../forecast/ForecastOverview.tsx";
import type { RouteEnergyRead } from "../forecast/useRouteEnergy.ts";
import { passengerStopLabel, type MapContext } from "../map/mapModel.ts";
import { useTripMap } from "../map/useTripMap.ts";

export function Overview({
  snap,
  token,
  connected,
  navigate,
  forecast,
  tripMap,
  arrival,
  review,
  reviewLabel,
}: {
  snap: Snapshot;
  token: string;
  connected: boolean;
  navigate: (t: Tab) => void;
  forecast: RouteEnergyRead;
  tripMap?: MapContext | null;
  arrival?: string | null;
  review?: () => void;
  reviewLabel?: string;
}) {
  const [selection, setSelection] = useState("battery.pack.soc"),
    [windowS, setWindowS] = useState(60);
  const selected =
    snap.items.find((i) => i.signal_id === selection) ?? snap.items[0];
  const newest = Math.max(
    0,
    ...snap.items.map((i) => i.observation?.sim_time_s ?? 0),
  );
  const forecastRecord = forecast.data?.items.find(
    ({ fence }) => fence === forecast.data?.current_fence,
  );
  const progress = connected ? snap.trip_progress : undefined;
  const remaining = useTripMap(
    snap.run_id,
    snap.asset_id,
    token,
    Boolean(!progress && connected && forecast.current && forecastRecord),
    forecastRecord,
  );
  const remainingStop = forecast.current
    ? remaining?.context?.features.find(
        ({ properties }) =>
          properties.layer === "stops" &&
          (tripMap?.features.some(
            (stop) =>
              `${properties.id}`.endsWith(`:${stop.properties.id}`) &&
              stop.properties.layer === "stops",
          ) ??
            true),
      )
    : undefined;
  const stopName = progress
    ? passengerStopLabel(tripMap, progress.next_stop_id)
    : remainingStop
      ? (tripMap?.features.find(
          ({ properties }) =>
            properties.layer === "stops" &&
            remainingStop.properties.id.endsWith(`:${properties.id}`),
        )?.properties.name ?? remainingStop.properties.name)
      : null;
  const distance = snap.items.find(
    ({ signal_id }) => signal_id === "motion.distance",
  );
  const distanceM =
    connected &&
    distance?.quality === "good" &&
    ["fresh", "paused"].includes(distance.freshness)
      ? distance.observation?.sample.value
      : null;
  const remainingM = progress
    ? progress.remaining_m
    : forecast.current?.summary?.remaining_distance_m;
  return (
    <>
      <section className="panel trip-overview" aria-label="Trip progress">
        <SectionHeading
          eyebrow="TRIP PROGRESS"
          icon="send"
          tone="teal"
          title={
            tripMap?.features.find(
              ({ properties }) => properties.layer === "route",
            )?.properties.name ?? "Current journey"
          }
        />
        <dl>
          <div>
            <dt>Distance traveled</dt>
            <dd>
              {distanceM == null
                ? "Unavailable"
                : `${value(distanceM / 1000)} km`}
            </dd>
          </div>
          <div>
            <dt>Estimated distance remaining</dt>
            <dd>
              {remainingM == null
                ? "Unavailable"
                : `${value(remainingM / 1000)} km`}
            </dd>
          </div>
          <div>
            <dt>Estimated time to arrival</dt>
            <dd>{arrival ?? "Unavailable"}</dd>
          </div>
          {stopName && (
            <div>
              <dt>
                {progress
                  ? progress.at_passenger_stop
                    ? "At passenger stop"
                    : "Next passenger stop"
                  : "Next planned passenger stop at forecast cutoff"}
              </dt>
              <dd>{stopName}</dd>
            </div>
          )}
        </dl>
        <button
          type="button"
          className={review ? "primary" : "secondary"}
          onClick={review ?? (() => navigate("Map"))}
        >
          {review ? reviewLabel : "View route"}
          <Icon name={review ? "Energy" : "arrow"} />
        </button>
      </section>
      <SummaryMetrics snap={snap} connected={connected} />
      <div className="overview-grid">
        <section className="panel trend-panel">
          <div className="panel-head">
            <div>
              <p className="eyebrow">LIVE TREND</p>
              <h2>
                {selected
                  ? measurementName(selected.signal_id)
                  : "Awaiting readings"}
              </h2>
            </div>
            <div className="trend-controls">
              <label>
                <span title="Choose the reading shown in the live chart">
                  Signal
                </span>
                <select
                  aria-label="Trend signal"
                  value={selected?.signal_id ?? ""}
                  onChange={(e) => setSelection(e.target.value)}
                >
                  {snap.items.map((i) => (
                    <option key={i.signal_id} value={i.signal_id}>
                      {measurementName(i.signal_id)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span title="Set how much recent working time the live chart shows">
                  Window
                </span>
                <select
                  aria-label="History window"
                  value={windowS}
                  onChange={(e) => setWindowS(Number(e.target.value))}
                >
                  <option value={60}>Last 60 s</option>
                  <option value={300}>Last 5 min</option>
                  <option value={1800}>Last 30 min</option>
                </select>
              </label>
            </div>
          </div>
          {selected && (
            <Trend
              snap={snap}
              selected={selected}
              token={token}
              windowS={windowS}
            />
          )}
        </section>
        <aside className="stack">
          <ForecastOverview
            forecast={forecast}
            connected={connected}
            open={() => navigate("Energy")}
          />
          <section className="panel gradient charcoal">
            <h2>Source state</h2>
            <div className="source-value">
              <Icon
                name={
                  snap.items[0]?.source_state === "paused"
                    ? "pause"
                    : "connection"
                }
              />
              {snap.items[0]?.source_state ?? "Unknown"}
            </div>
            <dl className="key-values">
              <dt>Working time</dt>
              <dd>{value(newest)} s</dd>
              <dt>Connection</dt>
              <dd>{connected ? "Connected" : "Disconnected"}</dd>
              <dt>Vehicle</dt>
              <dd>{snap.asset_id}</dd>
            </dl>
          </section>
          <section className="panel">
            <h2>Measurement coverage</h2>
            <p className="muted">
              {snap.items.length} registered ·{" "}
              {
                snap.items.filter((i) => connected && i.freshness === "fresh")
                  .length
              }{" "}
              fresh
            </p>
            <div className="coverage-strip">
              {snap.items.map((i) => (
                <span
                  key={i.signal_id}
                  title={`${label(i.signal_id)} · ${i.quality} · ${connected ? i.freshness : "stale"}`}
                  className={
                    i.quality !== "good"
                      ? "unknown"
                      : connected && i.freshness === "fresh"
                        ? "fresh"
                        : "old"
                  }
                />
              ))}
            </div>
            <div className="coverage-caption">
              {snap.items.filter((i) => i.quality === "good").length} good ·{" "}
              {
                snap.items.filter((i) => !connected || i.freshness === "stale")
                  .length
              }{" "}
              stale
            </div>
          </section>
        </aside>
      </div>
      <div className="quick-links">
        {(["Measurements", "History", "Map"] as const).map((t) => (
          <button key={t} onClick={() => navigate(t)}>
            <Icon name={t} />
            {t === "Measurements"
              ? "Explore measurements"
              : t === "History"
                ? "Open history"
                : "View route"}
            <Icon name="arrow" />
          </button>
        ))}
      </div>
    </>
  );
}
