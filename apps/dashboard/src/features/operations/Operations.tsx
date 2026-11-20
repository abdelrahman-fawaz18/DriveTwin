import { Icon } from "../../ui/Icon.tsx";
import { SectionHeading } from "../../ui/SectionHeading.tsx";
import { Bars } from "../../ui/Chart.tsx";
import { useEffect, useRef, useState } from "react";
import { Loading, Spinner } from "../../ui/Loading.tsx";
import type { Snapshot } from "../../monitoring/model.ts";
import { time, value } from "../../monitoring/model.ts";
import { tripEventLabel, type TripEvent } from "../trip/tripReview.ts";
type Event = {
  revision: number;
  kind: string;
  occurred_at: string;
  detail: Record<string, unknown>;
};
type Metrics = {
  available: boolean;
  observed_at?: string;
  broker_connected?: boolean;
  queue?: Record<string, number>;
  volume_free_bytes?: number;
  volume_total_bytes?: number;
  spool_file_bytes?: number;
  pending_byte_limit?: number;
  dropped_records?: number | null;
  overflow_indication?: boolean | null;
  storage?: {
    physical_quota_enforced: boolean;
    mode: string;
    critical_fault: {
      healthy: boolean;
      first_reason: string | null;
      last_reason: string | null;
      occurrences: number;
      valid_copies: number;
    };
  } | null;
};
type Health = {
  run_id: string;
  asset_id: string;
  accepted_events: number;
  accepted_last_30_s: number;
  ingest_events_per_s_30s: number;
  as_of: string;
  rejections: { reason: string; count: number }[];
  sender: Metrics;
  receiver: Metrics;
  history_available: boolean;
};
const eventTitles: Record<string, string> = {
  registration: "Vehicle registered",
  source_state: "Vehicle source state changed",
  quality_transition: "Reading quality changed",
  source_session: "New vehicle source session",
};
function activityLabel(event: Event) {
  return (
    eventTitles[event.kind.replaceAll("-", "_")] ??
    event.kind.replaceAll(/[_-]/g, " ")
  );
}
export function Operations({
  snap,
  token,
  view,
  connected,
  connectionEvents,
  tripEvents = [],
  inspectEvent,
  openMap,
}: {
  snap: Snapshot;
  token: string;
  view: "Activity" | "System";
  connected: boolean;
  connectionEvents: { at: string; message: string }[];
  tripEvents?: TripEvent[];
  inspectEvent?: (event: TripEvent) => void;
  openMap?: (event: TripEvent) => void;
}) {
  const [health, setHealth] = useState<Health | null>(null),
    [events, setEvents] = useState<Event[]>([]),
    [error, setError] = useState(""),
    [next, setNext] = useState<number | null>(null),
    [anchor, setAnchor] = useState("");
  const [refresh, setRefresh] = useState(0),
    [paging, setPaging] = useState(false),
    [limited, setLimited] = useState(false);
  const [eventKind, setEventKind] = useState("all"),
    [eventFrom, setEventFrom] = useState(""),
    [eventTo, setEventTo] = useState("");
  const [selectedEvent, setSelectedEvent] = useState<number | null>(null);
  const [eventPage, setEventPage] = useState(0);
  const generation = useRef(0);
  const [loading, setLoading] = useState(true);
  const latest = useRef(snap);
  latest.current = snap;
  useEffect(() => {
    const own = new AbortController();
    let stopped = false;
    let inflight = false;
    generation.current++;
    setPaging(false);
    setLimited(false);
    setNext(null);
    setHealth(null);
    setLoading(true);
    setEvents([]);
    setError("");
    async function load() {
      if (inflight) return;
      inflight = true;
      const current = latest.current;
      try {
        const endpoint =
          view === "System"
            ? "/v1/system-health?" +
              new URLSearchParams({ asset_id: current.asset_id })
            : "/v1/events?" +
              new URLSearchParams({
                asset_id: current.asset_id,
                as_of: current.cursor,
              });
        const r = await fetch(endpoint, {
          headers: { Authorization: `Bearer ${token}` },
          signal: AbortSignal.any([own.signal, AbortSignal.timeout(10000)]),
          cache: "no-store",
        });
        if (!r.ok) throw Error();
        const body = await r.json();
        if (
          body.run_id !== current.run_id ||
          body.asset_id !== current.asset_id
        )
          throw Error();
        if (stopped) return;
        if (view === "System") setHealth(body);
        else {
          if (body.as_of !== current.cursor || !Array.isArray(body.items))
            throw Error();
          setEvents(body.items);
          setNext(body.next);
          setAnchor(current.cursor);
        }
        setError("");
      } catch {
        if (!stopped) {
          setError(
            "Operational view unavailable. Previously displayed diagnostics are not current.",
          );
          setHealth(null);
          setEvents([]);
        }
      } finally {
        inflight = false;
        if (!stopped) setLoading(false);
      }
    }
    void load();
    const timer =
      view === "System" ? setInterval(() => void load(), 5000) : undefined;
    return () => {
      stopped = true;
      generation.current++;
      clearInterval(timer);
      own.abort();
    };
  }, [snap.run_id, snap.asset_id, token, view, refresh]);
  async function more() {
    if (!next || paging) return;
    const ownGeneration = generation.current;
    setPaging(true);
    try {
      const r = await fetch(
        "/v1/events?" +
          new URLSearchParams({
            asset_id: snap.asset_id,
            as_of: anchor,
            before: String(next),
          }),
        { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
      );
      const body = await r.json();
      if (
        !r.ok ||
        body.run_id !== snap.run_id ||
        body.asset_id !== snap.asset_id ||
        body.as_of !== anchor ||
        !Array.isArray(body.items)
      )
        throw Error();
      if (ownGeneration !== generation.current) return;
      const merged = [...events, ...body.items];
      setEvents(merged.slice(0, 500));
      setLimited(merged.length >= 500 && body.next !== null);
      setNext(merged.length >= 500 ? null : body.next);
    } catch {
      if (ownGeneration === generation.current)
        setError("Could not load older recorded events.");
    } finally {
      if (ownGeneration === generation.current) setPaging(false);
    }
  }
  const s = health?.sender,
    r = health?.receiver;
  const visibleEvents = events.filter((event) => {
    const stamp = Date.parse(event.occurred_at);
    return (
      (eventKind === "all" || event.kind === eventKind) &&
      (!eventFrom || stamp >= Date.parse(eventFrom + "Z")) &&
      (!eventTo || stamp <= Date.parse(eventTo + "Z"))
    );
  });
  const eventDetail =
    visibleEvents.find((e) => e.revision === selectedEvent) ?? visibleEvents[0];
  const pageIndex = Math.min(
    eventPage,
    Math.max(0, Math.ceil(visibleEvents.length / 8) - 1),
  );
  const bytes = (n?: number) =>
    n === undefined ? "Unavailable" : value(n / 1024 / 1024) + " MiB";
  const sourceState = connected
    ? (snap.items[0]?.source_state ?? "unknown")
    : "disconnected";
  const healthSummary = !connected
    ? [
        "Live readings interrupted",
        "Reconnect to see new readings. Check the current session in the launcher if the interruption persists.",
        "warning",
      ]
    : !health
      ? [
          loading ? "Checking system health" : "System status unavailable",
          loading
            ? "Checking the delivery path."
            : "No current health report was received.",
          "neutral",
        ]
      : s?.storage?.critical_fault && !s.storage.critical_fault.healthy
        ? [
            "Sender storage needs attention",
            "Delivery may be interrupted. Review storage and accounting below.",
            "warning",
          ]
        : sourceState === "disconnected"
          ? [
              "Vehicle readings interrupted",
              "The vehicle source disconnected. Check the delivery path for details.",
              "warning",
            ]
          : (s?.available && s.broker_connected === false) ||
              (r?.available && r.broker_connected === false)
            ? [
                "Delivery interrupted",
                "The broker connection is down. Check the pending queue below.",
                "warning",
              ]
            : !health.history_available
              ? [
                  "History unavailable",
                  "The service did not confirm that recorded readings can be queried.",
                  "warning",
                ]
              : sourceState === "paused" || sourceState === "finished"
                ? [
                    sourceState === "paused" ? "Trip paused" : "Trip finished",
                    "Recorded readings are available.",
                    "neutral",
                  ]
                : !s?.available || !r?.available
                  ? [
                      "Some service statuses are unavailable",
                      "Monitoring is connected. Sender or ingestion metrics could not be verified.",
                      "neutral",
                    ]
                  : [
                      "Delivery path connected",
                      "Monitoring and history are available for this vehicle.",
                      "good",
                    ];
  const components = [
    {
      name: "Source",
      icon: "Fleet",
      state: sourceState,
      detail: "Registered source state",
    },
    {
      name: "Sender",
      icon: "send",
      state: s?.available ? "available" : "unavailable",
      detail: "Authenticated queue metrics",
    },
    {
      name: "Broker",
      icon: "layers",
      state:
        s?.available && r?.available
          ? s.broker_connected && r.broker_connected
            ? "connected"
            : "disconnected"
          : "unavailable",
      detail: "Sender and receiver connections",
    },
    {
      name: "Ingestion",
      icon: "download",
      state: r?.available ? "available" : "unavailable",
      detail: "Authenticated receiver metrics",
    },
    {
      name: "Database",
      icon: "storage",
      state: health?.history_available ? "readable" : "unavailable",
      detail: "History query result",
    },
    {
      name: "API",
      icon: "code",
      state: health ? "available" : "unavailable",
      detail: "Scoped health response",
    },
    {
      name: "Browser",
      icon: "monitor",
      state: connected ? "connected" : "disconnected",
      detail: "Live update connection",
    },
  ];
  return (
    <>
      {loading && (
        <Loading
          label={
            view === "System"
              ? "Loading system health…"
              : "Loading recorded activity…"
          }
        />
      )}
      {error && (
        <p role="alert" className="notice warning">
          {error}
        </p>
      )}
      {view === "Activity" ? (
        <>
          {tripEvents.length > 0 && (
            <section
              className="panel operator-events"
              aria-label="Trip decisions and observations"
            >
              <div className="panel-head">
                <div>
                  <h2>Trip decisions and observations</h2>
                  <p className="fine">
                    Captured in this dashboard tab. Times follow the trip’s
                    working clock.
                  </p>
                </div>
              </div>
              <ol className="trip-timeline">
                {[...tripEvents]
                  .sort(
                    (a, b) =>
                      a.working - b.working ||
                      Date.parse(a.at) - Date.parse(b.at),
                  )
                  .map((event) => (
                    <li key={event.id}>
                      <div>
                        <strong>{tripEventLabel(event)}</strong>
                        <span>
                          {value(event.working)} working s · seen{" "}
                          {time(event.at)}
                        </span>
                      </div>
                      <div className="event-actions">
                        {inspectEvent && (
                          <button
                            type="button"
                            onClick={() => inspectEvent(event)}
                          >
                            Inspect readings
                          </button>
                        )}
                        {openMap && event.bay && (
                          <button type="button" onClick={() => openMap(event)}>
                            View stop on map
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
              </ol>
            </section>
          )}
          <section className="panel">
            <div className="panel-head">
              <SectionHeading
                eyebrow="DURABLE SOURCE & DATA EVENTS"
                title="Recorded activity"
                icon="Activity"
                tone="indigo"
              />
              <button
                className="secondary"
                onClick={() => setRefresh((n) => n + 1)}
              >
                {loading && <Spinner />} Refresh activity
              </button>
            </div>
            <div className="filters">
              <label>
                Event type
                <select
                  value={eventKind}
                  onChange={(event) => setEventKind(event.target.value)}
                >
                  <option value="all">All event types</option>
                  {Array.from(new Set(events.map((event) => event.kind)))
                    .sort()
                    .map((kind) => (
                      <option key={kind}>{kind}</option>
                    ))}
                </select>
              </label>
              <label>
                From (UTC)
                <input
                  type="datetime-local"
                  step="1"
                  value={eventFrom}
                  onChange={(event) => setEventFrom(event.target.value)}
                />
              </label>
              <label>
                To (UTC)
                <input
                  type="datetime-local"
                  step="1"
                  value={eventTo}
                  onChange={(event) => setEventTo(event.target.value)}
                />
              </label>
              <button
                className="secondary"
                onClick={() => {
                  setEventKind("all");
                  setEventFrom("");
                  setEventTo("");
                }}
              >
                Clear filters
              </button>
            </div>
            <p className="query-note">
              {visibleEvents.length} of {events.length} loaded events match.
              Load older events to extend the filtered set.
            </p>
            <div className="activity-grid">
              <div>
                <ol className="event-timeline">
                  {visibleEvents
                    .slice(pageIndex * 8, pageIndex * 8 + 8)
                    .map((e) => (
                      <li key={e.revision}>
                        <button
                          className={`event-row ${eventDetail?.revision === e.revision ? "selected" : ""}`}
                          onClick={() => setSelectedEvent(e.revision)}
                        >
                          <span
                            className={`event-symbol ${e.kind.includes("quality") ? "amber" : "cyan"}`}
                          >
                            <Icon
                              name={
                                e.kind.includes("quality")
                                  ? "alert"
                                  : "Activity"
                              }
                            />
                          </span>
                          <time>{time(e.occurred_at)}</time>
                          <span>
                            <strong>{e.kind.replaceAll("_", " ")}</strong>
                            <small>
                              Recorded source event · revision {e.revision}
                            </small>
                          </span>
                          <Icon name="chevron" />
                        </button>
                      </li>
                    ))}
                </ol>
                {!visibleEvents.length && !loading && (
                  <p className="empty">No recorded events in this view.</p>
                )}
                <div className="pagination">
                  <button
                    disabled={pageIndex === 0}
                    onClick={() => setEventPage(pageIndex - 1)}
                  >
                    Previous events
                  </button>
                  <span>
                    {pageIndex + 1} /{" "}
                    {Math.max(1, Math.ceil(visibleEvents.length / 8))}
                  </span>
                  <button
                    disabled={(pageIndex + 1) * 8 >= visibleEvents.length}
                    onClick={() => setEventPage(pageIndex + 1)}
                  >
                    Next events
                  </button>
                </div>
              </div>
              <aside className="stack">
                <section className="panel tinted violet">
                  <h2>Event details</h2>
                  {eventDetail ? (
                    <>
                      <h3>{activityLabel(eventDetail)}</h3>
                      <dl className="key-values">
                        <dt>Observed</dt>
                        <dd>{time(eventDetail.occurred_at)}</dd>
                        <dt>Vehicle</dt>
                        <dd>{snap.asset_id}</dd>
                        <dt>Revision</dt>
                        <dd>{eventDetail.revision}</dd>
                      </dl>
                      <pre className="event-detail">
                        {JSON.stringify(eventDetail.detail, null, 2)}
                      </pre>
                    </>
                  ) : (
                    <p>Select an event to inspect its details.</p>
                  )}
                </section>
                <section className="panel gradient charcoal">
                  <h2>Recording</h2>
                  <dl className="key-values">
                    <dt>Vehicle</dt>
                    <dd>{snap.asset_id}</dd>
                    <dt>Working time</dt>
                    <dd>
                      {value(
                        Math.max(
                          0,
                          ...snap.items.map(
                            (i) => i.observation?.sim_time_s ?? 0,
                          ),
                        ),
                      )}{" "}
                      s
                    </dd>
                    <dt>Source</dt>
                    <dd>{sourceState}</dd>
                  </dl>
                </section>
              </aside>
            </div>
            {next && (
              <button
                className="secondary"
                onClick={() => void more()}
                disabled={paging}
                aria-busy={paging}
              >
                {paging ? (
                  <>
                    <Spinner /> Loading older events…
                  </>
                ) : (
                  "Load older events"
                )}
              </button>
            )}
            <p className="query-note">
              Snapshot-bound · newest first. Refresh to include newer events.{" "}
              {limited && "Showing the first 500 events in this view. "}
              Events are recorded from this deployment's event-tracking
              migration onward. Earlier source states are not reconstructed.
            </p>
          </section>
          <section className="panel local-events">
            <div className="panel-head">
              <div>
                <p className="eyebrow">THIS BROWSER PAGE ONLY</p>
                <h2>Connection activity</h2>
              </div>
            </div>
            <ul>
              {connectionEvents.map((e, n) => (
                <li key={n}>
                  {time(e.at)} · {e.message}
                </li>
              ))}
            </ul>
          </section>
        </>
      ) : (
        <>
          <section
            className={`panel health-summary ${healthSummary[2]}`}
            aria-label="System status"
          >
            <Icon name="System" />
            <div>
              <h2>{healthSummary[0]}</h2>
              <p>{healthSummary[1]}</p>
            </div>
          </section>
          <div className="metrics-row system-metrics">
            <section className="metric metric-card gradient pink">
              <Icon name="layers" />
              <p>Accepted observation events</p>
              <div className="metric-value">
                {value(health?.accepted_events)}
              </div>
              <p>Current run / selected vehicle</p>
            </section>
            <section className="metric metric-card gradient violet">
              <Icon name="Energy" />
              <p>Recent ingest rate</p>
              <div className="metric-value">
                {value(health?.ingest_events_per_s_30s)}
                <span>events/s</span>
              </div>
              <p>Committed arrivals / fixed 30 s window</p>
            </section>
            <section className="metric metric-card gradient cyan">
              <Icon name="storage" />
              <p>Sender queue pending</p>
              <div className="metric-value">
                {value(s?.available ? s.queue?.pending : undefined)}
              </div>
              <p>
                {s?.available
                  ? "Authenticated sender measurement"
                  : "Metrics unavailable"}
              </p>
            </section>
            <section className="metric metric-card gradient charcoal">
              <Icon name="shield" />
              <p>Rejected records</p>
              <div className="metric-value">
                {health
                  ? health.rejections.reduce((n, q) => n + q.count, 0)
                  : "—"}
              </div>
              <p>Identity-bound quarantined inputs</p>
            </section>
          </div>
          <section className="panel delivery-panel">
            <div className="panel-head">
              <h2>Delivery path</h2>
              {health?.as_of && (
                <span className="fine">Observed {time(health.as_of)}</span>
              )}
            </div>
            <div className="delivery-path">
              {components.map((c, n) => (
                <div className="delivery-node" key={c.name}>
                  <span
                    className={`node-icon ${["pink", "violet", "cyan", "amber", "pink", "teal", "cyan"][n]}`}
                  >
                    <Icon name={c.icon} size={26} />
                  </span>
                  <strong>{c.name}</strong>
                  <span
                    className={`status ${["unknown", "unavailable", "disconnected"].includes(c.state) ? "amber" : c.state === "paused" ? "violet" : ""}`}
                  >
                    {c.state === "unavailable" || c.state === "unknown"
                      ? "Status unavailable"
                      : c.state}
                  </span>
                  <small>{c.detail}</small>
                  {n < components.length - 1 && (
                    <span className="node-arrow">→</span>
                  )}
                </div>
              ))}
            </div>
            <details className="raw">
              <summary>Status sources</summary>
              <p>
                These states describe the selected vehicle's delivery path.
                Container process, CPU, and memory status are not collected by
                this API.
              </p>
              <p>
                Sender metrics: {time(s?.observed_at)} · Receiver metrics:{" "}
                {time(r?.observed_at)}
              </p>
            </details>
          </section>
          <div className="system-grid">
            <section className="panel system-details">
              <div className="panel-head">
                <SectionHeading
                  eyebrow="SERVICE & STORAGE OBSERVATIONS"
                  title="Storage and accounting"
                  icon="storage"
                  tone="slate"
                />
                <span className="muted">Observed {time(health?.as_of)}</span>
              </div>
              <dl className="health-grid">
                <dt>Browser update connection</dt>
                <dd>
                  {connected
                    ? "Connected"
                    : "Disconnected · displayed measurements stale"}
                </dd>
                <dt>History / read API</dt>
                <dd>
                  {health?.history_available ? "Available" : "Unavailable"}
                </dd>
                <dt>Receiver → broker</dt>
                <dd>
                  {r?.available
                    ? r.broker_connected
                      ? "Connected"
                      : "Disconnected"
                    : "Metrics unavailable"}
                </dd>
                <dt>Sender → broker</dt>
                <dd>
                  {s?.available
                    ? s.broker_connected
                      ? "Connected"
                      : "Disconnected"
                    : "Metrics unavailable"}
                </dd>
                <dt>Sender queue outcomes</dt>
                <dd>
                  {s?.available ? JSON.stringify(s.queue) : "Unavailable"}
                </dd>
                <dt>Queue file size</dt>
                <dd>
                  {s?.available ? bytes(s.spool_file_bytes) : "Unavailable"}
                </dd>
                <dt>Queue pending-byte limit</dt>
                <dd>
                  {s?.available ? bytes(s.pending_byte_limit) : "Unavailable"}
                </dd>
                <dt>Linux spool volume free / total</dt>
                <dd>
                  {s?.available
                    ? `${bytes(s.volume_free_bytes)} / ${bytes(s.volume_total_bytes)}`
                    : "Unavailable"}
                </dd>
                <dt>Dropped-record counter</dt>
                <dd>
                  {s?.available && typeof s.dropped_records === "number"
                    ? `${value(s.dropped_records)} confirmed lost · ${value(s.queue?.evicted_unconfirmed)} evictions unresolved`
                    : "Unknown · no current instrumented count"}
                </dd>
                <dt>Telemetry overflow</dt>
                <dd>
                  {s?.available && typeof s.overflow_indication === "boolean"
                    ? s.overflow_indication
                      ? "Observed · retained eviction audit includes original identities and times"
                      : "No overflow observed in this recording"
                    : "Unknown · no current instrumented status"}
                </dd>
                <dt>Physical storage quota</dt>
                <dd>
                  {s?.available && s.storage?.physical_quota_enforced
                    ? `Verified separate bounded filesystems · ${s.storage.mode}`
                    : "No current verified bounded-storage measurement"}
                </dd>
                <dt>Critical storage fault</dt>
                <dd>
                  {s?.available && s.storage
                    ? s.storage.critical_fault.healthy
                      ? "No latched fault · two valid status copies"
                      : `Fault retained · first: ${s.storage.critical_fault.first_reason}; latest: ${s.storage.critical_fault.last_reason}; occurrences: ${value(s.storage.critical_fault.occurrences)}`
                    : "Unknown · no current instrumented status"}
                </dd>
                <dt>Remote clock accuracy</dt>
                <dd>
                  Unqualified · source clock checks use the aligned local host
                </dd>
              </dl>
            </section>
            <section className="panel">
              <div className="panel-head">
                <div>
                  <p className="eyebrow">ADMISSION & CLOCK DIAGNOSTICS</p>
                  <h2>Rejection reasons</h2>
                </div>
              </div>
              {health?.rejections.length ? (
                <Bars
                  unit="records"
                  items={health.rejections.map((q, n) => ({
                    label: q.reason,
                    value: q.count,
                    color: ["#8552ba", "#d9277f"][n % 2]!,
                  }))}
                />
              ) : (
                <p className="query-note">
                  {health
                    ? "No identity-bound rejected records for this vehicle."
                    : "Diagnostics unavailable."}
                </p>
              )}
            </section>
          </div>
          <section className="panel">
            <div className="panel-head">
              <h2>Delivery queue</h2>
              <span className="fine">Current sender counters</span>
            </div>
            {s?.available && s.queue ? (
              <Bars
                items={Object.entries(s.queue).map(([label, value], n) => ({
                  label,
                  value,
                  color: ["#d9277f", "#8552ba", "#368e85", "#b9862a"][n % 4]!,
                }))}
                unit="records"
              />
            ) : (
              <p className="empty">Sender metrics unavailable.</p>
            )}
          </section>
        </>
      )}
    </>
  );
}
