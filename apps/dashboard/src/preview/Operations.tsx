import { useEffect, useState } from "react";
import { Bars, Chart } from "../ui/Chart.tsx";
import { Icon } from "../ui/Icon.tsx";
import {
  clock,
  initialEvents,
  number,
  palette,
  series,
  signalById,
  signals,
  vehicleName,
} from "./data.ts";
import type { EventRecord, Vehicle } from "./data.ts";
import { Details, Empty, Panel, Status } from "./ui.tsx";
import type { PreviewContext } from "./ui.tsx";
import { PreviewMap } from "./PreviewMap.tsx";

export function Activity({
  c,
  events,
  browserEvents,
}: {
  c: PreviewContext;
  events: EventRecord[];
  browserEvents: { time: string; label: string }[];
}) {
  const [type, setType] = useState("All event types"),
    [selected, setSelected] = useState<EventRecord>(initialEvents[3]!),
    [page, setPage] = useState(0),
    [eventFrom, setEventFrom] = useState(0),
    [eventTo, setEventTo] = useState(100000);
  const filtered = events.filter(
    (e) =>
      (type === "All event types" || e.type === type) &&
      e.t >= eventFrom &&
      e.t <= eventTo,
  );
  return (
    <>
      <div className="filter-bar">
        <label>
          Event type
          <select
            value={type}
            onChange={(e) => {
              setType(e.target.value);
              setPage(0);
            }}
          >
            <option>All event types</option>
            <option>Source</option>
            <option>Quality</option>
          </select>
        </label>
        <label>
          From (s)
          <input
            type="number"
            min="0"
            value={eventFrom}
            onChange={(e) => {
              setEventFrom(Number(e.target.value));
              setPage(0);
            }}
          />
        </label>
        <label>
          To (s)
          <input
            type="number"
            min="0"
            value={eventTo}
            onChange={(e) => {
              setEventTo(Number(e.target.value));
              setPage(0);
            }}
          />
        </label>
        <span className="muted push-right">Working time 0–{c.time} s</span>
      </div>
      <div className="activity-grid">
        <Panel title="Recorded events">
          <ol className="event-timeline">
            {filtered.slice(page * 5, page * 5 + 5).map((e) => (
              <li key={e.id}>
                <button
                  className={`event-row ${selected.id === e.id ? "selected" : ""}`}
                  onClick={() => setSelected(e)}
                >
                  <span className={`event-symbol ${e.tone}`}>
                    <Icon
                      name={
                        e.title.includes("paused")
                          ? "pause"
                          : e.type === "Quality"
                            ? "alert"
                            : "play"
                      }
                      size={18}
                    />
                  </span>
                  <time>{e.t} s</time>
                  <span>
                    <strong>{e.title}</strong>
                    <small>{e.detail}</small>
                  </span>
                  <Icon name="chevron" size={15} />
                </button>
              </li>
            ))}
          </ol>
          {!filtered.length && (
            <Empty
              title="No recorded events"
              text="Choose another event type."
            />
          )}
          <div className="pagination">
            <button disabled={!page} onClick={() => setPage(page - 1)}>
              Previous
            </button>
            <span>
              {page + 1} / {Math.max(1, Math.ceil(filtered.length / 5))}
            </span>
            <button
              disabled={(page + 1) * 5 >= filtered.length}
              onClick={() => setPage(page + 1)}
            >
              Next
            </button>
          </div>
        </Panel>
        <aside className="stack">
          <Panel title="Event details">
            <div className="event-detail-title">
              <span className={`event-symbol ${selected.tone}`}>
                <Icon
                  name={selected.type === "Quality" ? "alert" : "Activity"}
                />
              </span>
              <h3>{selected.title}</h3>
            </div>
            <dl className="key-values">
              <dt>Working time</dt>
              <dd>{selected.t} s</dd>
              <dt>Vehicle</dt>
              <dd>{vehicleName(c.vehicle)}</dd>
              <dt>Source</dt>
              <dd>Vehicle adapter</dd>
              <dt>Category</dt>
              <dd>{selected.type}</dd>
              <dt>Recording</dt>
              <dd>Preview sample</dd>
            </dl>
            <p className="detail-copy">{selected.detail}</p>
          </Panel>
          <Panel title="Recording" className="gradient charcoal">
            <dl className="key-values">
              <dt>Vehicle</dt>
              <dd>{vehicleName(c.vehicle)}</dd>
              <dt>Working time</dt>
              <dd>{clock(c.time)}</dd>
              <dt>Source</dt>
              <dd>{c.mode === "Paused" ? "Paused" : "Running"}</dd>
            </dl>
          </Panel>
        </aside>
      </div>
      <Panel
        title="Browser connection events"
        action={<span className="muted">This session</span>}
      >
        <div className="browser-events">
          {browserEvents.map((e, i) => (
            <div key={i}>
              <Icon name="connection" size={18} />
              <time>{e.time}</time>
              <span>{e.label}</span>
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

export function System({ c }: { c: PreviewContext }) {
  const disconnected = c.mode === "Disconnected",
    missing = c.mode === "Missing data";
  const components = [
    {
      label: "Source",
      icon: "Fleet",
      state: missing ? "Unknown" : c.mode === "Paused" ? "Paused" : "Running",
    },
    {
      label: "Sender",
      icon: "send",
      state: missing ? "Unavailable" : "Connected",
    },
    {
      label: "Broker",
      icon: "layers",
      state: missing ? "Unavailable" : "Connected",
    },
    {
      label: "Ingestion",
      icon: "download",
      state: missing ? "Unavailable" : "Available",
    },
    {
      label: "Database",
      icon: "storage",
      state: missing ? "Unavailable" : "Available",
    },
    {
      label: "API",
      icon: "code",
      state: disconnected ? "Unavailable" : "Available",
    },
    {
      label: "Browser",
      icon: "monitor",
      state: disconnected ? "Disconnected" : "Connected",
    },
  ];
  return (
    <>
      <div className="metrics-row">
        <MetricLite
          title="Accepted events"
          value={missing ? null : c.time}
          tone="pink"
          icon="layers"
        />
        <MetricLite
          title="Ingest rate"
          value={missing ? null : c.mode === "Paused" ? 0 : 1}
          unit="events/s"
          tone="violet"
          icon="Energy"
        />
        <MetricLite
          title="Queue pending"
          value={missing ? null : 0}
          tone="cyan"
          icon="storage"
        />
        <MetricLite
          title="Rejected records"
          value={missing ? null : 3}
          tone="charcoal"
          icon="shield"
        />
      </div>
      <Panel title="Delivery path">
        <div className="delivery-path">
          {components.map((item, i) => (
            <div className="delivery-node" key={item.label}>
              <span
                className={`node-icon ${["pink", "violet", "cyan", "amber", "pink", "teal", "cyan"][i]}`}
              >
                <Icon name={item.icon} size={26} />
              </span>
              <strong>{item.label}</strong>
              <Status
                label={item.state}
                tone={
                  ["Unavailable", "Disconnected", "Unknown"].includes(
                    item.state,
                  )
                    ? "amber"
                    : "teal"
                }
              />
              {i < components.length - 1 && (
                <span className="node-arrow">→</span>
              )}
            </div>
          ))}
        </div>
        <p className="fine">
          Metrics age {number(c.age, 1)} s · Selected vehicle and recording
        </p>
      </Panel>
      <div className="equal-grid">
        <Panel title="Storage and accounting">
          <dl className="key-values">
            <dt>Queue file</dt>
            <dd>{missing ? "Unavailable" : "1.2 MiB"}</dd>
            <dt>Pending-byte limit</dt>
            <dd>{missing ? "Unavailable" : "64 MiB"}</dd>
            <dt>Volume free / total</dt>
            <dd>{missing ? "Unavailable" : "18.4 / 64 GiB"}</dd>
            <dt>Confirmed lost</dt>
            <dd>{missing ? "Unknown" : "0"}</dd>
            <dt>Unresolved evictions</dt>
            <dd>{missing ? "Unknown" : "0"}</dd>
            <dt>Overflow</dt>
            <dd>{missing ? "Unknown" : "No indication"}</dd>
            <dt>Physical quota</dt>
            <dd>Unavailable</dd>
            <dt>Critical storage status</dt>
            <dd>Unavailable</dd>
          </dl>
        </Panel>
        <Panel title="Rejection reasons">
          {missing ? (
            <Empty
              title="Diagnostics unavailable"
              text="Waiting for an authenticated metrics sample."
            />
          ) : (
            <Bars
              unit="records"
              maximum={3}
              items={[
                { label: "Invalid value", value: 2, color: palette.violet },
                { label: "Timestamp", value: 1, color: palette.pink },
              ]}
            />
          )}
          <p className="fine">
            Identity-bound records in the selected recording.
          </p>
        </Panel>
      </div>
      <Details title="Connection and clock details">
        <dl className="metadata-grid">
          <dt>History queries</dt>
          <dd>{disconnected ? "Unavailable" : "Available"}</dd>
          <dt>Sender to broker</dt>
          <dd>{missing ? "Unavailable" : "Connected"}</dd>
          <dt>Receiver to broker</dt>
          <dd>{missing ? "Unavailable" : "Connected"}</dd>
          <dt>Update cadence</dt>
          <dd>1 second</dd>
          <dt>Remote clock accuracy</dt>
          <dd>Unqualified</dd>
          <dt>Origin</dt>
          <dd>Preview sample</dd>
        </dl>
      </Details>
    </>
  );
}
function MetricLite({
  title,
  value,
  unit = "",
  tone,
  icon,
}: {
  title: string;
  value: number | null;
  unit?: string;
  tone: string;
  icon: string;
}) {
  return (
    <section className={`metric-card gradient ${tone}`}>
      <div className="metric-title">
        <Icon name={icon} size={22} />
        {title}
      </div>
      <div className="metric-number">
        {number(value, 1)}
        <span>{unit}</span>
      </div>
      <small>Current recording</small>
    </section>
  );
}

type CommandState =
  | "Pending"
  | "Reserved"
  | "Executed"
  | "Effect verified"
  | "Outcome unknown"
  | "No effect"
  | "Canceled"
  | "Superseded"
  | "Expired";
interface Command {
  id: number;
  speed: number;
  reason: string;
  state: CommandState;
  created: number;
  deadline: number;
}
export function Operator({ c }: { c: PreviewContext }) {
  const [connected, setConnected] = useState(false),
    [speed, setSpeed] = useState("4.0"),
    [reason, setReason] = useState(""),
    [commands, setCommands] = useState<Command[]>([]),
    [notice, setNotice] = useState("");
  const [station, setStation] = useState(""),
    [missionReason, setMissionReason] = useState(""),
    [missions, setMissions] = useState<
      { station: string; reason: string; time: number }[]
    >([]),
    [outcome, setOutcome] = useState("Verified effect"),
    [profile, setProfile] = useState("Direct");
  const current = commands[0],
    pending = current?.state === "Pending";
  const eligible = c.mode === "Running" && connected;
  useEffect(() => {
    if (current?.state === "Pending" && c.time > current.deadline)
      setCommands((previous) =>
        previous.map((cmd, i) => (i ? cmd : { ...cmd, state: "Expired" })),
      );
  }, [c.time, current]);
  const request = () => {
    const v = Number(speed);
    if (!Number.isFinite(v) || v < 2 || v > 5 || !reason.trim()) {
      setNotice("Enter a speed from 2 to 5 m/s and a reason.");
      return;
    }
    if (!eligible) {
      setNotice("Fresh readings and an active operator session are required.");
      return;
    }
    setCommands((previous) => [
      {
        id: Date.now(),
        speed: v,
        reason: reason.trim(),
        state: "Pending",
        created: c.time,
        deadline: c.time + 60,
      },
      ...previous.map((cmd) =>
        cmd.state === "Pending"
          ? { ...cmd, state: "Superseded" as const }
          : cmd,
      ),
    ]);
    setNotice("Speed request recorded in this preview.");
  };
  const advance = () => {
    if (!current || !eligible) return;
    const next: CommandState =
      current.state === "Pending"
        ? "Reserved"
        : current.state === "Reserved"
          ? outcome === "Unknown execution"
            ? "Outcome unknown"
            : "Executed"
          : outcome === "No effect"
            ? "No effect"
            : "Effect verified";
    setCommands((previous) =>
      previous.map((cmd, i) => (i ? cmd : { ...cmd, state: next })),
    );
  };
  const stage = current?.state,
    handoff = [
      "Reserved",
      "Executed",
      "Effect verified",
      "No effect",
      "Outcome unknown",
    ].includes(stage ?? ""),
    executed = ["Executed", "Effect verified", "No effect"].includes(
      stage ?? "",
    );
  return (
    <>
      <div className="operator-toolbar">
        <Status
          label={connected ? "Operator session active" : "Reader session"}
          tone="violet"
        />
        <span className="muted">
          {vehicleName(c.vehicle)} · {profile} profile
        </span>
        <button
          className="push-right"
          onClick={() => {
            setConnected(!connected);
            setNotice("");
          }}
        >
          {connected ? "Disconnect operator" : "Connect sample operator"}
        </button>
      </div>
      <div className="preview-options">
        <label>
          Preview authority
          <select value={profile} onChange={(e) => setProfile(e.target.value)}>
            <option>Direct</option>
            <option>Arbitrated</option>
          </select>
        </label>
        <label>
          Preview outcome
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option>Verified effect</option>
            <option>No effect</option>
            <option>Unknown execution</option>
          </select>
        </label>
        <span>Actions update sample records in this browser.</span>
      </div>
      {!connected ? (
        <Panel>
          <Empty
            title="Operator access"
            text="Connect the sample operator session to review command controls."
          />
          <button className="primary" onClick={() => setConnected(true)}>
            Connect sample operator
          </button>
        </Panel>
      ) : (
        <>
          <div className="operator-grid">
            <Panel title={pending ? "Pending speed request" : "Speed request"}>
              {profile === "Arbitrated" ? (
                <div>
                  <p>
                    Selected authority <strong>Operator · generation 4</strong>
                  </p>
                  <p className="fine">
                    The sealed decision controls this command scope.
                  </p>
                </div>
              ) : (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    request();
                  }}
                >
                  <div className="form-row">
                    <label>
                      Speed cap (m/s)
                      <input
                        type="number"
                        min="2"
                        max="5"
                        step="0.1"
                        value={speed}
                        onChange={(e) => setSpeed(e.target.value)}
                        required
                      />
                      <small>Allowed 2–5 m/s</small>
                    </label>
                    <label>
                      Reason
                      <input
                        value={reason}
                        maxLength={160}
                        placeholder="Reason for the speed request"
                        onChange={(e) => setReason(e.target.value)}
                        required
                      />
                    </label>
                  </div>
                  <div className="button-row">
                    <button className="primary" disabled={!eligible || handoff}>
                      {pending ? "Replace pending cap" : "Request speed cap"}
                    </button>
                    {pending && (
                      <button
                        type="button"
                        onClick={() => {
                          setCommands((previous) =>
                            previous.map((cmd, i) =>
                              i ? cmd : { ...cmd, state: "Canceled" },
                            ),
                          );
                          setNotice("Pending request canceled before handoff.");
                        }}
                      >
                        Cancel pending command
                      </button>
                    )}
                  </div>
                </form>
              )}
              {profile === "Arbitrated" && pending && (
                <button
                  onClick={() =>
                    setCommands((previous) =>
                      previous.map((cmd, i) =>
                        i ? cmd : { ...cmd, state: "Canceled" },
                      ),
                    )
                  }
                >
                  Cancel pending command
                </button>
              )}
            </Panel>
            <Panel title="Command status" className="gradient charcoal">
              <div className="source-value">{stage ?? "Ready"}</div>
              <p>
                {stage === "Pending"
                  ? "Awaiting executor handoff"
                  : stage === "Outcome unknown"
                    ? "Execution could not be confirmed"
                    : stage === "No effect"
                      ? "Independent readings did not show an effect"
                      : stage === "Effect verified"
                        ? "Observed readings confirm the requested cap"
                        : "Selected vehicle and recording"}
              </p>
              <dl className="key-values">
                <dt>Scope</dt>
                <dd>{vehicleName(c.vehicle)}</dd>
                <dt>Deadline</dt>
                <dd>
                  {current
                    ? `${Math.max(0, current.deadline - c.time)} s remaining`
                    : "—"}
                </dd>
              </dl>
            </Panel>
          </div>
          <Panel title="Command lifecycle">
            <div className="command-stages">
              {[
                {
                  name: "Accepted",
                  value: !!current,
                  text: current ? "Recorded" : "Awaiting request",
                },
                {
                  name: "Reserved",
                  value: handoff,
                  text: handoff ? "Handoff recorded" : "Pending",
                },
                {
                  name: "Execution",
                  value: executed,
                  text: executed
                    ? "Executed"
                    : stage === "Outcome unknown"
                      ? "Unknown"
                      : "No execution reported",
                },
                {
                  name: "Measured effect",
                  value: stage === "Effect verified",
                  text:
                    stage === "Effect verified"
                      ? "Verified"
                      : stage === "No effect"
                        ? "No effect"
                        : "Awaiting evidence",
                },
              ].map((s) => (
                <div key={s.name}>
                  <span className={s.value ? "stage reached" : "stage"}>
                    <Icon name={s.value ? "check" : "clock"} size={18} />
                    {s.name}
                  </span>
                  <small>{s.text}</small>
                </div>
              ))}
            </div>
            {current &&
              ["Pending", "Reserved", "Executed"].includes(current.state) && (
                <button
                  className="subtle"
                  disabled={!eligible}
                  onClick={advance}
                >
                  Preview next stage <Icon name="arrow" size={16} />
                </button>
              )}
          </Panel>
          <div className="equal-grid">
            <Panel title="Mission intent">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!station || !missionReason.trim()) return;
                  setMissions((previous) => [
                    { station, reason: missionReason, time: c.time },
                    ...previous,
                  ]);
                  setNotice("Mission intent recorded for review.");
                  setMissionReason("");
                }}
              >
                <label>
                  Mission station
                  <select
                    value={station}
                    required
                    onChange={(e) => setStation(e.target.value)}
                  >
                    <option value="">Choose a station</option>
                    <option>Charging bay A</option>
                    <option>Charging bay B</option>
                  </select>
                </label>
                <label>
                  Reason
                  <input
                    required
                    maxLength={160}
                    value={missionReason}
                    onChange={(e) => setMissionReason(e.target.value)}
                    placeholder="Reason for the charging stop"
                  />
                </label>
                <button className="primary violet-button" disabled={!eligible}>
                  Record mission intent
                </button>
              </form>
              {missions.map((m, i) => (
                <div className="mission-record" key={i}>
                  <Status label="Recorded for review" />
                  <strong>{m.station}</strong>
                  <small>
                    {m.reason} · {m.time} s
                  </small>
                </div>
              ))}
            </Panel>
            <Panel title="Command history">
              {!commands.length ? (
                <Empty
                  title="No commands yet"
                  text="A speed request will appear here after admission."
                />
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Request</th>
                        <th>State</th>
                        <th>Effect</th>
                      </tr>
                    </thead>
                    <tbody>
                      {commands.map((cmd) => (
                        <tr key={cmd.id}>
                          <td>
                            <strong>{cmd.speed} m/s cap</strong>
                            <small>{cmd.reason}</small>
                            <details>
                              <summary>Details</summary>
                              <p>
                                Requested at {cmd.created} s. Deadline{" "}
                                {cmd.deadline} s. {vehicleName(c.vehicle)}.
                              </p>
                            </details>
                          </td>
                          <td>{cmd.state}</td>
                          <td>
                            {cmd.state === "Effect verified"
                              ? "Verified"
                              : cmd.state === "No effect"
                                ? "No effect"
                                : [
                                      "Canceled",
                                      "Superseded",
                                      "Expired",
                                    ].includes(cmd.state)
                                  ? "No assessment"
                                  : "Awaiting evidence"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          </div>
        </>
      )}
      {notice && (
        <div className="notice teal" role="status">
          <Icon name="check" />
          {notice}
        </div>
      )}
    </>
  );
}

export function Replay({
  vehicle,
  onClose,
}: {
  vehicle: Vehicle;
  onClose: () => void;
}) {
  const [playhead, setPlayhead] = useState(100),
    [playing, setPlaying] = useState(false),
    [speed, setSpeed] = useState(1),
    [signal, setSignal] = useState("motion.speed");
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(
      () =>
        setPlayhead((p) => {
          if (p + speed >= 180) {
            setPlaying(false);
            return 180;
          }
          return p + speed;
        }),
      1000,
    );
    return () => clearInterval(timer);
  }, [playing, speed]);
  const selected = signalById(signal);
  return (
    <div
      className="replay-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="replay-title"
    >
      <div className="replay-window">
        <div className="page-heading">
          <div>
            <span className="eyebrow">
              Recorded sample · {vehicleName(vehicle)}
            </span>
            <h1 id="replay-title">Recording replay</h1>
          </div>
          <button
            autoFocus
            className="icon-button"
            aria-label="Close replay"
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </div>
        <div className="replay-controls">
          <button className="primary" onClick={() => setPlaying(!playing)}>
            <Icon name={playing ? "pause" : "play"} size={17} />
            {playing ? "Pause" : "Play"}
          </button>
          <button
            onClick={() => {
              setPlayhead(0);
              setPlaying(false);
            }}
          >
            <Icon name="reset" size={17} />
            Reset
          </button>
          <span>{clock(playhead)} / 00:03:00</span>
          <input
            aria-label="Replay playhead"
            type="range"
            min="0"
            max="180"
            step="1"
            value={playhead}
            onChange={(e) => setPlayhead(Number(e.target.value))}
          />
          <select
            aria-label="Replay speed"
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
          >
            <option value=".5">0.5×</option>
            <option value="1">1×</option>
            <option value="2">2×</option>
          </select>
        </div>
        <PreviewMap time={playhead} vehicle={vehicle} stale={false} compact />
        <Panel
          title="Recorded readings"
          action={
            <select
              aria-label="Replay signal"
              value={signal}
              onChange={(e) => setSignal(e.target.value)}
            >
              {signals.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          }
        >
          <Chart
            points={series(signal, 0, 180, vehicle, true)}
            color={selected.color}
            unit={selected.unit}
            cursor={playhead}
            onSeek={setPlayhead}
            height={175}
            label={`${selected.label} recording`}
          />
        </Panel>
        <Details title="All recorded measurements">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Signal</th>
                  <th>Value at playhead</th>
                  <th>Unit</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={s.id}>
                    <td>{s.label}</td>
                    <td>
                      {number(
                        series(
                          s.id,
                          Math.floor(playhead),
                          Math.floor(playhead),
                          vehicle,
                          true,
                        )[0]?.value ?? null,
                        3,
                      )}
                    </td>
                    <td>{s.unit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Details>
      </div>
    </div>
  );
}
