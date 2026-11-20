import { signalColor } from "../app/presentation.ts";
import { useId, useEffect, useRef, useState } from "react";
import {
  label,
  parseHistory,
  segments,
  time,
  value,
} from "../monitoring/model.ts";
import type { Point, Reading, Snapshot } from "../monitoring/model.ts";
import { tripEventLabel, type TripEvent } from "../features/trip/tripReview.ts";
export function Trend({
  snap,
  selected,
  token,
  windowS,
  bounds,
  pinned = false,
  cursorS,
  markerS,
  display,
  onCursor,
  provided,
  tripEvents = [],
}: {
  snap: Snapshot;
  selected: Reading;
  token: string;
  windowS: number;
  bounds?: { from: number; to: number };
  pinned?: boolean;
  cursorS?: number;
  markerS?: number;
  tripEvents?: TripEvent[];
  display?: { factor: number; unit: string; color?: string };
  onCursor?: (t: number) => void;
  provided?: {
    items: Point[];
    runs: Point[][];
    loading: boolean;
    error: string;
    rawCount: number;
    asOf: string;
  };
}) {
  const [loadedPoints, setPoints] = useState<Point[]>([]),
    [loadError, setError] = useState(""),
    [isLoading, setLoading] = useState(true);
  const [anchor, setAnchor] = useState("");
  const latest = useRef(snap);
  latest.current = snap;
  useEffect(() => {
    if (provided) return;
    let stopped = false,
      request: AbortController | undefined;
    setPoints([]);
    setError("");
    setLoading(true);
    async function load() {
      request?.abort();
      const own = new AbortController();
      request = own;
      const current = latest.current;
      const end =
        bounds?.to ??
        Math.max(
          0,
          ...current.items.map((i) => i.observation?.sim_time_s ?? 0),
        );
      const query = new URLSearchParams({
        asset_id: current.asset_id,
        signal_id: selected.signal_id,
        as_of: current.cursor,
        from_s: String(bounds?.from ?? Math.max(0, end - windowS)),
        to_s: String(end),
        limit: "500",
      });
      const collected: Point[] = [];
      let next: string | null = null;
      const timeout = setTimeout(() => own.abort(), 12000);
      try {
        do {
          if (next) query.set("after", next);
          const response = await fetch(`/v1/history?${query}`, {
            headers: { Authorization: `Bearer ${token}` },
            signal: own.signal,
            cache: "no-store",
          });
          if (!response.ok) throw Error("History unavailable");
          const page = parseHistory(
            await response.json(),
            current,
            selected.signal_id,
            current.cursor,
          );
          collected.push(...page.items);
          next = page.next;
          if (collected.length >= 5000 && next)
            throw Error(
              "Window exceeds 5,000 raw readings. Choose a shorter window.",
            );
        } while (next);
        if (!stopped && request === own) {
          setPoints(collected);
          setAnchor(current.as_of);
          setError("");
        }
      } catch {
        if (!stopped && request === own) {
          setPoints([]);
          setError(
            "History unavailable or window exceeds the 5,000-reading limit. Choose a shorter window or wait for reconnection.",
          );
        }
      } finally {
        clearTimeout(timeout);
        if (!stopped && request === own) setLoading(false);
      }
    }
    void load();
    const interval = pinned ? undefined : setInterval(() => void load(), 4000);
    return () => {
      stopped = true;
      clearInterval(interval);
      request?.abort();
    };
  }, [
    snap.run_id,
    snap.asset_id,
    selected.signal_id,
    token,
    windowS,
    bounds?.from,
    bounds?.to,
    pinned,
    pinned ? snap.cursor : undefined,
    provided !== undefined,
  ]);
  const gradientId = useId();
  const color = display?.color ?? signalColor(selected.signal_id);
  const factor = display?.factor ?? 1,
    unit = display?.unit ?? selected.definition.unit;
  const points = provided?.items ?? loadedPoints,
    error = provided?.error ?? loadError,
    loading = provided?.loading ?? isLoading;
  const runs =
      provided?.runs ?? segments(points, selected.definition.freshness_s),
    good = runs.flat();
  const xmin = bounds?.from ?? points[0]?.sim_time_s ?? 0,
    xmax = Math.max(xmin + 1, bounds?.to ?? points.at(-1)?.sim_time_s ?? 1);
  const values = good.map((p) => p.sample.value! * factor);
  const low = values.length ? Math.min(...values) : 0,
    high = values.length ? Math.max(...values) : 1;
  const pad = Math.max((high - low) * 0.12, Math.abs(high) * 0.005, 0.01),
    ymin = low - pad,
    ymax = high + pad;
  const x = (p: Point) => 62 + ((p.sim_time_s - xmin) / (xmax - xmin)) * 735;
  const y = (p: Point) =>
    202 - ((p.sample.value! * factor - ymin) / (ymax - ymin)) * 174;
  return (
    <>
      <div className="chart" aria-busy={loading}>
        {error ? (
          <p role="alert" className="empty">
            {error}
          </p>
        ) : !points.length ? (
          <p className="empty">
            {loading ? (
              <Loading label="Loading recorded readings…" />
            ) : (
              "No recorded readings in this window."
            )}
          </p>
        ) : (
          <svg
            onPointerMove={
              onCursor
                ? (e) => {
                    const rect = e.currentTarget.getBoundingClientRect();
                    onCursor(
                      xmin +
                        Math.max(
                          0,
                          Math.min(
                            1,
                            (((e.clientX - rect.left) / rect.width) * 840 -
                              62) /
                              735,
                          ),
                        ) *
                          (xmax - xmin),
                    );
                  }
                : undefined
            }
            viewBox="0 0 840 250"
            role="img"
            aria-label={`${label(selected.signal_id)} history in ${unit}. Invalid or missing readings and timing gaps break the line.`}
          >
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color} stopOpacity="0.24" />
                <stop offset="100%" stopColor={color} stopOpacity="0.01" />
              </linearGradient>
            </defs>
            {[0, 0.5, 1].map((r) => (
              <g key={r}>
                <line
                  className="gridline"
                  x1="62"
                  x2="797"
                  y1={28 + 174 * r}
                  y2={28 + 174 * r}
                />
                <text x="50" y={32 + 174 * r} textAnchor="end">
                  {value(ymax - (ymax - ymin) * r)}
                </text>
              </g>
            ))}
            {runs.map((run, n) => (
              <g key={n}>
                {run.length > 1 && (
                  <polygon
                    points={`${x(run[0]!)},202 ${run.map((p) => `${x(p)},${y(p)}`).join(" ")} ${x(run.at(-1)!)},202`}
                    fill={`url(#${gradientId})`}
                  />
                )}
                <polyline
                  className="trace"
                  style={{ stroke: color }}
                  points={run.map((p) => `${x(p)},${y(p)}`).join(" ")}
                />
                {run.length === 1 && (
                  <circle
                    className="trace-dot"
                    style={{ fill: color }}
                    cx={x(run[0]!)}
                    cy={y(run[0]!)}
                    r="3"
                  />
                )}
              </g>
            ))}
            {cursorS !== undefined && (
              <line
                className="cursor-line"
                x1={62 + ((cursorS - xmin) / (xmax - xmin)) * 735}
                x2={62 + ((cursorS - xmin) / (xmax - xmin)) * 735}
                y1="28"
                y2="202"
              />
            )}
            {markerS !== undefined && markerS >= xmin && markerS <= xmax && (
              <g className="forecast-cutoff">
                <line
                  x1={62 + ((markerS - xmin) / (xmax - xmin)) * 735}
                  x2={62 + ((markerS - xmin) / (xmax - xmin)) * 735}
                  y1="28"
                  y2="202"
                  stroke="#bd3676"
                  strokeDasharray="5 4"
                />
                <text
                  x={Math.max(
                    165,
                    62 + ((markerS - xmin) / (xmax - xmin)) * 735 - 5,
                  )}
                  y="43"
                  textAnchor="end"
                >
                  Forecast cutoff
                </text>
              </g>
            )}
            {tripEvents
              .filter(({ working }) => working >= xmin && working <= xmax)
              .map((event) => {
                const x = 62 + ((event.working - xmin) / (xmax - xmin)) * 735;
                return (
                  <g
                    key={event.id}
                    className="history-event-marker"
                    aria-label={tripEventLabel(event)}
                  >
                    <title>
                      {tripEventLabel(event)} at {value(event.working)} working
                      s
                    </title>
                    <line x1={x} x2={x} y1="28" y2="202" />
                    <circle cx={x} cy="28" r="4" />
                  </g>
                );
              })}
            <text x="62" y="231">
              {value(xmin)} s
            </text>
            <text x="797" y="231" textAnchor="end">
              {value(xmax)} s
            </text>
            <text x="430" y="247" textAnchor="middle">
              Working time · seconds
            </text>
            <text x="62" y="15">
              {unit}
            </text>
          </svg>
        )}
      </div>
      {cursorS !== undefined && (
        <p className="cursor-reading" data-testid="cursor-reading">
          Cursor {value(cursorS)} s · {label(selected.signal_id)}:{" "}
          {(() => {
            const nearest = points
              .filter(
                (p) =>
                  Math.abs(p.sim_time_s - cursorS) <=
                  selected.definition.sample_period_s / 2,
              )
              .sort(
                (a, b) =>
                  Math.abs(a.sim_time_s - cursorS) -
                    Math.abs(b.sim_time_s - cursorS) ||
                  b.source_epoch - a.source_epoch ||
                  b.stream_seq - a.stream_seq,
              )[0];
            return nearest &&
              nearest.sample.value !== null &&
              (!nearest.sample.quality || nearest.sample.quality === "good")
              ? `${value(nearest.sample.value * factor)} ${unit} at ${value(nearest.sim_time_s)} s`
              : provided
                ? "no retained point near cursor; raw export contains the complete readings"
                : "gap / no eligible reading";
          })()}
        </p>
      )}
      <div className="chart-foot">
        <span>
          {provided
            ? `${points.length} original plot points of ${provided.rawCount} readings`
            : `${points.length} raw readings`}{" "}
          · gaps preserved · extrema retained in plot
        </span>
        <span>History as of {time(provided?.asOf || anchor || undefined)}</span>
      </div>
      <details className="raw">
        <summary>Inspect recorded values and provenance</summary>
        <div className="table-scroll">
          <table>
            <caption>
              {provided
                ? "Latest 100 retained original plot points. Download raw readings for the complete interval."
                : "Latest 100 readings in the selected window."}{" "}
              Stored units; no interpolation.
            </caption>
            <thead>
              <tr>
                <th>Working time</th>
                <th>Value ({selected.definition.unit})</th>
                <th>Quality</th>
                <th>Observed (UTC)</th>
                <th>Received (UTC)</th>
                <th>Provenance / reason</th>
              </tr>
            </thead>
            <tbody>
              {points.slice(-100).map((p) => (
                <tr key={p.event_id}>
                  <td>{value(p.sim_time_s)} s</td>
                  <td>{value(p.sample.value)}</td>
                  <td>{p.sample.quality ?? "good"}</td>
                  <td>{time(p.observed_at)}</td>
                  <td>{time(p.received_at)}</td>
                  <td>
                    {p.sample.provenance}
                    {p.sample.reason && ` · ${p.sample.reason}`}
                    {p.sample.raw_value !== undefined &&
                      ` · raw ${value(p.sample.raw_value)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}
import { Loading } from "./Loading.tsx";
