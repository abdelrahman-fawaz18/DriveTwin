import { StrictMode, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  advance,
  fixes,
  history,
  project,
  step,
  verifySession,
} from "./replayModel.ts";
import type { ReplaySession } from "./replayModel.ts";
import { label, segments, time, value } from "../../monitoring/model.ts";
import { useMapScene } from "../map/useMapScene.ts";
import "../../ui/theme.css";
import "../../style.css";
import { SectionHeading } from "../../ui/SectionHeading.tsx";
import "../../ui/loading.css";
import { Spinner } from "../../ui/Loading.tsx";
import { MapLayers, MapLegend } from "../map/MapLayers.tsx";
import { chargingMarkers } from "../map/mapModel.ts";
import { SignalPicker } from "../history/SignalPicker.tsx";
import { Chart } from "../../ui/Chart.tsx";
import { measurementName, signalColor } from "../../app/presentation.ts";
import "../forecast/route-energy.css";
import { Logo } from "../../ui/Icon.tsx";
import "./replay.css";

interface Sources {
  source_run_id: string;
  assets: {
    asset_id: string;
    packets: number;
    from_s: number | null;
    to_s: number | null;
  }[];
}
async function boundedBody(response: Response) {
  const reader = response.body?.getReader();
  if (!reader) throw Error("Replay body unavailable");
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.length;
      if (total > 16 * 1024 * 1024)
        throw Error("Replay exceeds browser package limit");
      chunks.push(value);
    }
  } finally {
    await reader.cancel();
  }
  const raw = new Uint8Array(total);
  let at = 0;
  for (const chunk of chunks) {
    raw.set(chunk, at);
    at += chunk.length;
  }
  return raw;
}
function ReplayMap({
  session,
  observedThrough,
}: {
  session: ReplaySession;
  observedThrough: number;
}) {
  const points = useMemo(
      () => fixes(session, observedThrough),
      [session, observedThrough],
    ),
    last = points.at(-1);
  const position = useMemo(
    () =>
      last?.coordinates
        ? {
            coordinates: last.coordinates,
            freshness: "recorded replay position",
          }
        : null,
    [last],
  );
  const approvedBay = useMemo(
    () =>
      session.packets.findLast(
        ({ body }) =>
          body.sim_time_s <= observedThrough &&
          typeof body.annotations?.charging_bay === "string" &&
          [
            "approaching",
            "waiting_for_charger",
            "charging",
            "target_reached",
            "motion_resumed",
          ].includes(body.annotations?.charging_effect ?? ""),
      )?.body.annotations?.charging_bay,
    [session, observedThrough],
  );
  const charging = useMemo(
    () => chargingMarkers(session.context, { approved: approvedBay }),
    [session.context, approvedBay],
  );
  const [layers, setLayers] = useState([
    "roads",
    "route",
    "stops",
    "stations",
    "endpoints",
    "trajectory",
    "labels",
  ]);
  const { host, map } = useMapScene(
    session.context,
    points,
    Math.max(
      session.catalog.signals["location.latitude"]?.freshness_s ?? 5,
      session.catalog.signals["location.longitude"]?.freshness_s ?? 5,
    ),
    position,
    layers,
    false,
    charging,
  );
  return (
    <section className="panel replay-map">
      <div className="panel-head">
        <div>
          <p className="eyebrow">RECORDING · THROUGH PLAYHEAD ONLY</p>
          <h2>Recorded trajectory</h2>
        </div>
        <button
          className="secondary"
          disabled={!session.context}
          title="Fit the full recorded route in the map."
          onClick={() => {
            const b = session.context?.bbox;
            if (b)
              map.current?.fitBounds([
                [b[1], b[0]],
                [b[3], b[2]],
              ]);
          }}
        >
          Fit map
        </button>
      </div>
      <div className="replay-map-layout">
        <div className="replay-map-display">
          {session.context ? (
            <div
              ref={host}
              className="vehicle-map"
              role="region"
              aria-label="Replay map through playhead"
            />
          ) : (
            <p className="empty">
              No recorded map context is assigned to this asset.
            </p>
          )}
          <p data-testid="replay-position">
            {last?.coordinates
              ? `Recorded position at ${value(last.sim_time_s)} s`
              : points.some((p) => p.coordinates)
                ? "Location unavailable at playhead · last valid recorded position shown"
                : "No recorded position at playhead"}{" "}
            · {points.filter((p) => p.coordinates).length} recorded points
            through playhead · gaps preserved
          </p>
          <MapLegend
            items={[
              { tone: "observed", label: "Recorded trajectory" },
              { tone: "planned", label: "Planned route" },
              { tone: "stop", label: "Passenger stop" },
              { tone: "charge", label: "Charging bay", icon: "ϟ" },
              ...(approvedBay
                ? [
                    {
                      tone: "approved",
                      label: "Approved charging bay",
                      icon: "ϟ",
                    },
                  ]
                : []),
              ...(approvedBay &&
              session.context?.features.some(
                ({ properties }) =>
                  properties.layer === "detour" &&
                  properties.id === approvedBay,
              )
                ? [{ tone: "detour", label: "Charging detour" }]
                : []),
            ]}
          />
        </div>
        <aside className="replay-map-options">
          <h3>Map layers</h3>
          <MapLayers
            layers={layers}
            setLayers={setLayers}
            items={[
              { id: "trajectory", label: "Recorded trajectory" },
              {
                id: "route",
                label: "Planned route",
                hint: "Show the assigned route and any approved charging detour reached in the recording.",
              },
              { id: "stops", label: "Passenger stops" },
              { id: "stations", label: "Charging bays" },
              {
                id: "roads",
                label: "Roads",
                related: ["labels"],
                hint: "Show the street backdrop and street labels.",
              },
            ]}
          />
        </aside>
      </div>
    </section>
  );
}
function Player({ session }: { session: ReplaySession }) {
  const [t, setT] = useState(session.from_s),
    [playing, setPlaying] = useState(false),
    [rate, setRate] = useState(1),
    [notice, setNotice] = useState(""),
    [selected, setSelected] = useState<string[]>([
      Object.keys(session.catalog.signals).includes("motion.speed")
        ? "motion.speed"
        : Object.keys(session.catalog.signals)[0]!,
    ]);
  const clock = useRef(t);
  clock.current = t;
  useEffect(() => {
    if (!playing) return;
    let last = performance.now(),
      frame = 0;
    const tick = (now: number) => {
      const next = advance(clock.current, now - last, rate, session.to_s);
      last = now;
      if (next.interrupted || document.hidden) {
        setPlaying(false);
        setNotice(
          "Playback paused after a scheduling interruption or hidden tab.",
        );
        return;
      }
      clock.current = next.time;
      setT(next.time);
      if (next.time >= session.to_s) {
        setPlaying(false);
        return;
      }
      frame = requestAnimationFrame(tick);
    };
    const hidden = () => {
      if (document.hidden) {
        setPlaying(false);
        setNotice("Playback paused while the tab was hidden.");
      }
    };
    frame = requestAnimationFrame(tick);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", hidden);
    };
  }, [playing, rate, session]);
  function seek(n: number) {
    setPlaying(false);
    setNotice("");
    clock.current = Math.max(session.from_s, Math.min(session.to_s, n));
    setT(clock.current);
  }
  const readings = useMemo(() => project(session, t), [session, t]);
  const span = Math.max(1, session.to_s - session.from_s);
  const progress = (100 * (t - session.from_s)) / span;
  const next = session.packets.findIndex((p) => p.body.sim_time_s > t);
  const reached = next < 0 ? session.packets.length : next;
  const observedThrough = session.packets[reached - 1]?.body.sim_time_s ?? -1;
  const charts = useMemo(
    () =>
      selected.map((id) => {
        const definition = session.catalog.signals[id];
        if (!definition) return null;
        const raw = history(session, id, observedThrough);
        const runs = segments(raw, definition.freshness_s, 500);
        const charge = id === "battery.pack.soc" && definition.unit === "1";
        const unit = charge ? "%" : definition.unit;
        const points: { t: number; value: number | null }[] = [
          { t: session.from_s, value: null },
        ];
        for (const run of runs) {
          if (run[0]) points.push({ t: run[0].sim_time_s, value: null });
          for (const point of run) {
            const value = point.sample.value;
            points.push({
              t: point.sim_time_s,
              value: charge && value !== null ? value * 100 : value,
            });
          }
        }
        points.push({ t: session.to_s, value: null });
        return { id, unit, points, count: raw.length };
      }),
    [session, selected, observedThrough],
  );
  return (
    <>
      <section className="panel replay-controls">
        <div className="panel-head">
          <SectionHeading
            eyebrow="Playback"
            title={`${session.asset_id} recording`}
            icon="play"
            tone="indigo"
          />
          <span className="replay-chip">
            {playing
              ? "PLAYING"
              : t === session.to_s
                ? "END OF RECORDING"
                : "PAUSED"}
          </span>
        </div>
        <div className="replay-clock">
          <output data-testid="replay-time">{t.toFixed(2)} s</output>
          <span> / {value(session.to_s)} working s</span>
          <span className="replay-packets">
            {reached} of {session.packets.length} observations
          </span>
        </div>
        <div className="replay-timeline">
          <label
            className="replay-slider"
            title="Drag to move through the recording. Use arrow keys for fine adjustment."
          >
            <span className="visually-hidden">Replay playhead</span>
            <input
              type="range"
              aria-label="Replay playhead"
              min={session.from_s}
              max={session.to_s}
              step="0.01"
              value={t}
              style={{
                background: `linear-gradient(to right, #b32869 ${progress}%, #e1e5ef ${progress}%)`,
              }}
              onChange={(e) => seek(Number(e.target.value))}
            />
          </label>
          <div className="replay-ticks">
            <span>{value(session.from_s)} s</span>
            <span>{value(session.to_s)} s</span>
          </div>
        </div>
        <div className="playback-buttons">
          <button
            className="primary"
            title="Play or pause this recording without affecting the trip"
            onClick={() => {
              setNotice("");
              if (t >= session.to_s) seek(session.from_s);
              setPlaying((p) => !p);
            }}
            disabled={session.to_s === session.from_s}
          >
            {playing ? "Pause" : "Play"}
          </button>
          <button
            className="secondary transport-step"
            title="Restart playback"
            aria-label="Restart"
            onClick={() => seek(session.from_s)}
          >
            ↺
          </button>
          <button
            className="secondary"
            aria-label="Previous observation"
            title="Previous observation"
            onClick={() => seek(step(session, t, -1))}
          >
            <span aria-hidden="true">‹</span>
          </button>
          <button
            className="secondary"
            aria-label="Next observation"
            title="Next observation"
            onClick={() => seek(step(session, t, 1))}
          >
            <span aria-hidden="true">›</span>
          </button>
          <button className="secondary" onClick={() => seek(session.to_s)}>
            Go to end
          </button>
          <label>
            Playback speed
            <select
              value={rate}
              onChange={(e) => setRate(Number(e.target.value))}
            >
              {[0.25, 0.5, 1, 2, 4, 8].map((n) => (
                <option key={n} value={n}>
                  {n}×
                </option>
              ))}
            </select>
          </label>
          <label
            className="precise-seek"
            title="Jump to an exact working second in the recording."
          >
            Seek to working seconds
            <input
              type="number"
              min={session.from_s}
              max={session.to_s}
              step="any"
              value={Number(t.toFixed(2))}
              onChange={(e) => {
                const n = e.target.valueAsNumber;
                if (Number.isFinite(n)) seek(n);
              }}
            />
          </label>
        </div>
        {notice && (
          <p className="fine" role="status">
            {notice}
          </p>
        )}
      </section>
      <section className="panel replay-analysis">
        <div className="panel-head">
          <div>
            <h2>Recorded signals</h2>
            <p className="fine">Charts follow the shared playhead.</p>
          </div>
          <SignalPicker
            items={readings.map((r) => ({
              id: r.signal_id,
              unit: r.definition.unit,
            }))}
            selected={selected}
            onSelect={setSelected}
          />
        </div>
        <div className="replay-charts">
          {charts.map((chart) => {
            if (!chart) return null;
            const { id, count, points, unit } = chart;
            const display = measurementName(id);
            const entry = readings.find((r) => r.signal_id === id)?.entry;
            return (
              <article className="replay-chart" key={id}>
                <div className="panel-head">
                  <h3>{display}</h3>
                  <span className="fine">{unit}</span>
                </div>
                <Chart
                  points={points}
                  color={signalColor(id)}
                  unit={unit}
                  cursor={t}
                  label={`${id} replay trend through playhead`}
                  height={195}
                  onSeek={seek}
                />
                <div className="replay-chart-foot">
                  <span className="fine">
                    {count} readings · gaps preserved
                  </span>
                  <details className="raw">
                    <summary>Inspect selected original observation</summary>
                    {entry ? (
                      <pre>
                        {JSON.stringify(
                          {
                            wire_sha256: entry.packet.wire_sha256,
                            source_revision: entry.packet.source_revision,
                            seed: entry.packet.seed,
                            event_id: entry.packet.body.event_id,
                            observed_at: entry.packet.body.observed_at,
                            received_at: entry.packet.received_at,
                            persisted_at: entry.packet.persisted_at,
                            sample: entry.sample,
                          },
                          null,
                          2,
                        )}
                      </pre>
                    ) : (
                      <p>No recorded value at this playhead.</p>
                    )}
                  </details>
                </div>
              </article>
            );
          })}
          {!selected.length && (
            <p className="empty">Choose signals to chart.</p>
          )}
        </div>
      </section>
      <ReplayMap
        key={session.replay_run_id}
        session={session}
        observedThrough={observedThrough}
      />
      <section className="panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">GENERIC CATALOG-DRIVEN READINGS</p>
            <h2>Replay measurements</h2>
          </div>
          <span>{readings.length} registered signals</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Signal / unit</th>
                <th>Recorded value</th>
                <th>Quality</th>
                <th>Age at playhead</th>
                <th>Recording recency</th>
              </tr>
            </thead>
            <tbody>
              {readings.map((r) => (
                <tr key={r.signal_id} data-replay-signal={r.signal_id}>
                  <td>
                    {label(r.signal_id)}
                    <small>
                      {r.signal_id} · {r.definition.unit}
                    </small>
                  </td>
                  <td className="number">
                    {r.quality === "good"
                      ? value(r.entry?.sample.value ?? null)
                      : "—"}
                  </td>
                  <td>{r.quality}</td>
                  <td>
                    {r.age === null ? "—" : value(r.age) + " s"}
                    {r.entry?.packet.seed && <small>Before-window seed</small>}
                  </td>
                  <td>{r.recency}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel replay-provenance">
        <p className="eyebrow">FROZEN COPY · ORIGINAL BYTES RETAINED</p>
        <h2>Session provenance</h2>
        <dl>
          <dt>Replay namespace</dt>
          <dd data-testid="replay-namespace">{session.namespace}</dd>
          <dt>Original source revision</dt>
          <dd>{session.source_cursor}</dd>
          <dt>Time basis</dt>
          <dd>{session.time_basis}</dd>
          <dt>Created</dt>
          <dd>{time(session.created_at)}</dd>
          <dt>Verified package SHA-256</dt>
          <dd data-testid="replay-digest">{session.digest}</dd>
          <dt>Catalog</dt>
          <dd>
            {session.catalog.catalog_version} ·{" "}
            {session.catalog.source_catalog_sha256}
          </dd>
          <dt>Original recipes</dt>
          <dd>{session.recipes.map((r) => r.sha256).join(" · ")}</dd>
        </dl>
        <p>
          Hashes check byte consistency. These are recorded measurements, not
          calibrated real-bus evidence. Late-arriving observations inside this
          frozen revision are placed at their original working time. Later
          ingress cannot alter this loaded session.
        </p>
      </section>
    </>
  );
}
const replayAccess = new URLSearchParams(location.hash.slice(1)).get("access");
if (location.hash) historyReplace();
function historyReplace() {
  window.history.replaceState(null, "", location.pathname + location.search);
}
function App() {
  const [credential, setCredential] = useState(replayAccess ?? ""),
    [token, setToken] = useState(""),
    [sources, setSources] = useState<Sources | null>(null),
    [asset, setAsset] = useState(""),
    [from, setFrom] = useState(0),
    [to, setTo] = useState(0),
    [session, setSession] = useState<ReplaySession | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const generation = useRef(0),
    active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  function begin() {
    active.current?.abort();
    active.current = new AbortController();
    generation.current++;
    setError("");
    setBusy(true);
    return { signal: active.current.signal, own: generation.current };
  }
  async function connect() {
    const { signal, own } = begin();
    try {
      const r = await fetch("/v1/replay/sources", {
        headers: { Authorization: `Bearer ${credential}` },
        signal,
        cache: "no-store",
      });
      if (!r.ok)
        throw Error(
          "Replay access expired or is outside this recording. Reopen it from the dashboard.",
        );
      const s = (await r.json()) as Sources;
      if (!s.source_run_id || !Array.isArray(s.assets) || !s.assets.length)
        throw Error("No authorized recordings");
      if (own !== generation.current) return;
      setSources(s);
      setToken(credential);
      setCredential("");
      choose(s.assets[0]!.asset_id, s);
    } catch (e) {
      if (own === generation.current)
        setError(e instanceof Error ? e.message : "Replay unavailable");
    } finally {
      if (own === generation.current) setBusy(false);
    }
  }
  useEffect(() => {
    if (replayAccess) void connect();
  }, []);
  function choose(id: string, s = sources) {
    setSession(null);
    setAsset(id);
    const row = s?.assets.find((a) => a.asset_id === id);
    setFrom(row?.from_s ?? 0);
    setTo(row?.to_s ?? 0);
  }
  async function open() {
    if (!sources) return;
    const { signal, own } = begin();
    setSession(null);
    try {
      const r = await fetch("/v1/replay/sessions", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          asset_id: asset,
          source_run_id: sources.source_run_id,
          from_s: from,
          to_s: to,
        }),
        signal,
        cache: "no-store",
      });
      if (!r.ok)
        throw Error(
          `Cannot create replay (${r.status}). Check the interval, permissions and package limits.`,
        );
      const s = await verifySession(
        await boundedBody(r),
        r.headers.get("X-Content-SHA256") ?? "",
        { asset, run: sources.source_run_id, from, to },
      );
      if (own === generation.current) setSession(s);
    } catch (e) {
      if (own === generation.current)
        setError(e instanceof Error ? e.message : "Replay verification failed");
    } finally {
      if (own === generation.current) setBusy(false);
    }
  }
  function close() {
    active.current?.abort();
    generation.current++;
    setToken("");
    setCredential("");
    setSources(null);
    setSession(null);
    setError("");
    setBusy(false);
  }
  return (
    <div className="replay-shell">
      <header className="replay-header">
        <a className="brand" href="/monitoring">
          <Logo />
          DriveTwin
        </a>
        <strong>REPLAY · RECORDING ANALYSIS</strong>
        {token && (
          <button className="secondary" onClick={close}>
            Close replay session
          </button>
        )}
      </header>
      <main>
        <div className="replay-intro">
          <h1>Trip replay</h1>
          <p>Explore the trip at your own pace.</p>
        </div>
        {!token ? (
          <section className="panel replay-login">
            <h2>
              {busy
                ? "Connecting to the recording"
                : "Open replay from the dashboard"}
            </h2>
            {busy ? (
              <p role="status">
                <Spinner /> Checking access…
              </p>
            ) : (
              <p>
                Sign in to the dashboard and select <strong>Open replay</strong>
                .
              </p>
            )}
          </section>
        ) : (
          <form
            className="panel replay-request"
            onSubmit={(e) => {
              e.preventDefault();
              void open();
            }}
          >
            <label>
              Recorded vehicle
              <select
                value={asset}
                disabled={busy}
                onChange={(e) => choose(e.target.value)}
              >
                {sources?.assets.map((a) => (
                  <option value={a.asset_id} key={a.asset_id}>
                    {a.asset_id} · {a.packets} observations
                  </option>
                ))}
              </select>
            </label>
            <label>
              From working seconds
              <input
                type="number"
                disabled={busy}
                min="0"
                step="any"
                value={from}
                onChange={(e) => {
                  setFrom(Number(e.target.value));
                  setSession(null);
                }}
                required
              />
            </label>
            <label>
              To working seconds
              <input
                type="number"
                disabled={busy}
                min={from}
                max={from + 86400}
                step="any"
                value={to}
                onChange={(e) => {
                  setTo(Number(e.target.value));
                  setSession(null);
                }}
                required
              />
            </label>
            <button
              disabled={busy}
              title="Load a verified copy of the selected interval. Up to 10,000 observations and 8 MiB."
            >
              {busy ? (
                <>
                  <Spinner /> Copying and verifying…
                </>
              ) : (
                "Load recording"
              )}
            </button>
          </form>
        )}
        {error && (
          <p className="replay-error" role="alert">
            {error}
          </p>
        )}
        {session && <Player key={session.replay_run_id} session={session} />}
      </main>
      <footer>
        DriveTwin · Replay format 1.0.0 · Local analysis · No dispatch
        capability
      </footer>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
