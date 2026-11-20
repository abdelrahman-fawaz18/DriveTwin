import { Icon } from "../../ui/Icon.tsx";
import { useCallback, useEffect, useRef, useState } from "react";
import { Loading } from "../../ui/Loading.tsx";

type Command = {
  command_id: string;
  state: string;
  accepted_at: string;
  reserved_at: string | null;
  recorded_at: string | null;
  changed_at?: string;
  replacement_command_id?: string;
  refusal_reason: string | null;
  result: { status: string } | null;
  effect: { status: string; maximum_commanded_m_s: number } | null;
  request: {
    parameters: { value: number; unit: string };
    expires_at: string;
    reason: string;
  };
};
type Timeline = {
  run_id: string;
  asset_id: string;
  server_time: string;
  arbitrated: boolean;
  authority: { selected_proposer: string; generation: number } | null;
  commands: Command[];
  stations: string[];
  missions: {
    intent: { intent_id: string; station_id: string; reason: string };
    recorded_at: string;
    station_catalog_sha256: string;
  }[];
};
type Action = { path: string; body: string };
class OperatorError extends Error {
  constructor(readonly status: number) {
    super(
      status === 401
        ? "The dashboard key was not accepted for operator access."
        : status === 404
          ? "Operator controls are unavailable on this deployment."
          : status === 409
            ? "The request conflicts with the current command. Check the refreshed timeline."
            : status === 403
              ? "This operator session is outside the selected run or vehicle."
              : status === 422
                ? "The request failed validation. Check the values and deadline."
                : "The command service could not confirm the request.",
    );
  }
}

const display = (value: string) => value.replaceAll("_", " ");
const time = (value: string) => new Date(value).toLocaleTimeString();

export function OperatorControls({
  run,
  asset,
  credential,
}: {
  run: string;
  asset: string;
  credential: string;
}) {
  const prefix = `/v1/operator/${encodeURIComponent(run)}/${encodeURIComponent(asset)}`;
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [speed, setSpeed] = useState("4");
  const [reason, setReason] = useState("");
  const [station, setStation] = useState("");
  const [missionReason, setMissionReason] = useState("");
  const [retry, setRetry] = useState<Action | null>(null);
  const session = useRef<AbortController | null>(null);
  const readSequence = useRef(0);

  const readTimeline = useCallback(
    async (key: string, signal: AbortSignal) => {
      const sequence = ++readSequence.current;
      const response = await fetch(`${prefix}/timeline`, {
        headers: { Authorization: `Bearer ${key}` },
        cache: "no-store",
        signal,
      });
      if (!response.ok) throw new OperatorError(response.status);
      const data: Timeline = await response.json();
      if (
        data.run_id !== run ||
        data.asset_id !== asset ||
        !Number.isFinite(Date.parse(data.server_time))
      ) {
        throw new OperatorError(403);
      }
      if (!signal.aborted && sequence === readSequence.current)
        setTimeline(data);
      return data;
    },
    [prefix, run, asset],
  );

  const disconnect = useCallback(() => {
    session.current?.abort();
    setTimeline(null);
    setRetry(null);
    setNotice("");
    setBusy(false);
  }, []);

  const reportError = useCallback(
    (failure: unknown) => {
      if (
        failure instanceof OperatorError &&
        (failure.status === 401 || failure.status === 403)
      )
        disconnect();
      setError(
        failure instanceof OperatorError
          ? failure.message
          : "Could not reach the command service.",
      );
    },
    [disconnect],
  );

  useEffect(() => {
    const active = new AbortController();
    session.current = active;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        await readTimeline(credential, active.signal);
      } catch (failure) {
        if (!active.signal.aborted) reportError(failure);
      }
      if (!active.signal.aborted) timer = setTimeout(() => void poll(), 2000);
    }
    void poll();
    return () => {
      active.abort();
      clearTimeout(timer);
    };
  }, [credential, readTimeline, reportError]);

  async function perform(
    kind: "speed" | "replace" | "cancel" | "mission" | "retry",
    id?: string,
  ) {
    const active = session.current;
    if (!active || busy || (retry && kind !== "retry")) return;
    setBusy(true);
    setError("");
    setNotice("");
    let action = kind === "retry" ? retry : null;
    try {
      if (!action) {
        if (kind === "cancel")
          action = { path: `commands/${id}/cancel`, body: "{}" };
        else {
          const latest = await readTimeline(credential, active.signal);
          const issued = Date.parse(latest.server_time);
          const scope = {
            run_id: run,
            asset_id: asset,
            issuer: "local-operator",
            mode: "simulation",
            issued_at: new Date(issued).toISOString(),
            expires_at: new Date(issued + 30000).toISOString(),
          };
          action =
            kind === "mission"
              ? {
                  path: "mission-intents",
                  body: JSON.stringify({
                    ...scope,
                    schema_version: "evdt-mission-intent/1.0.0",
                    intent_id: crypto.randomUUID(),
                    station_id: station,
                    reason: missionReason.trim(),
                  }),
                }
              : {
                  path:
                    kind === "replace" ? `commands/${id}/replace` : "commands",
                  body: JSON.stringify({
                    ...scope,
                    schema_version: "evdt-command/1.0.0",
                    command_id: crypto.randomUUID(),
                    capability: "set_speed_limit",
                    parameters: { value: Number(speed), unit: "m/s" },
                    reason: reason.trim(),
                    not_before_sim_s: 4,
                    expires_sim_s: 6,
                  }),
                };
        }
      }
      const response = await fetch(`${prefix}/${action.path}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${credential}`,
          "Content-Type": "application/json",
        },
        body: action.body,
        signal: active.signal,
      });
      if (!response.ok) throw new OperatorError(response.status);
      action = null;
      setRetry(null);
      setNotice("Operator request recorded.");
      await readTimeline(credential, active.signal);
    } catch (failure) {
      if (active.signal.aborted) return;
      const status = failure instanceof OperatorError ? failure.status : 0;
      setRetry(!status || status >= 500 ? action : null);
      reportError(failure);
      if (status === 409)
        await readTimeline(credential, active.signal).catch(reportError);
    } finally {
      if (!active.signal.aborted) setBusy(false);
    }
  }

  const pending = timeline?.commands.find(
    (command) => command.state === "queued",
  );
  const latestCommand = timeline?.commands[0];
  return (
    <section
      className="panel operator-panel"
      aria-label="Operator controls and command timeline"
    >
      <h2>Operator controls and command timeline</h2>
      {busy && credential && <Loading label="Submitting operator request…" />}
      {!timeline ? (
        !error && <Loading label="Loading operator controls…" />
      ) : (
        <>
          <p>
            {timeline.arbitrated
              ? "Arbitrated command profile"
              : "Direct command profile"}{" "}
            · {asset}
          </p>
          {timeline.authority && (
            <p>
              Selected role {timeline.authority.selected_proposer} · Authority
              number {timeline.authority.generation}
            </p>
          )}
          {!timeline.arbitrated && (pending || !timeline.commands.length) && (
            <form
              className="operator-form"
              onSubmit={(event) => {
                event.preventDefault();
                if (reason.trim())
                  void perform(
                    pending ? "replace" : "speed",
                    pending?.command_id,
                  );
              }}
            >
              <label>
                Speed cap (m/s)
                <input
                  type="number"
                  min="2"
                  max="5"
                  step="any"
                  required
                  value={speed}
                  onChange={(event) => setSpeed(event.target.value)}
                />
              </label>
              <label>
                Speed request reason
                <input
                  maxLength={160}
                  required
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <button
                className="primary"
                disabled={busy || !!retry || !reason.trim()}
              >
                {pending ? "Replace pending cap" : "Request speed cap"}
              </button>
            </form>
          )}
          {pending && (
            <button
              className="secondary"
              disabled={busy || !!retry}
              onClick={() => void perform("cancel", pending.command_id)}
            >
              Cancel pending command
            </button>
          )}
          {timeline.stations.length > 0 && (
            <form
              className="operator-form"
              onSubmit={(event) => {
                event.preventDefault();
                if (missionReason.trim()) void perform("mission");
              }}
            >
              <label>
                Mission station
                <select
                  required
                  value={station}
                  onChange={(event) => setStation(event.target.value)}
                >
                  <option value="">Choose a station</option>
                  {timeline.stations.map((choice) => (
                    <option key={choice}>{choice}</option>
                  ))}
                </select>
              </label>
              <label>
                Mission request reason
                <input
                  maxLength={160}
                  required
                  value={missionReason}
                  onChange={(event) => setMissionReason(event.target.value)}
                />
              </label>
              <button
                className="primary"
                disabled={busy || !!retry || !missionReason.trim()}
              >
                Record mission intent
              </button>
            </form>
          )}
          {retry && (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => void perform("retry")}
            >
              Retry the same request
            </button>
          )}
          {latestCommand && (
            <>
              <section className="panel gradient charcoal command-status">
                <Icon name="code" />
                <h3>Command status</h3>
                <strong>{display(latestCommand.state)}</strong>
                <p>{latestCommand.request.reason}</p>
                <small>
                  {asset} · Deadline {time(latestCommand.request.expires_at)}
                </small>
              </section>
              <section className="panel">
                <h3>Command lifecycle</h3>
                <div className="command-lifecycle">
                  {[
                    ["Request", `Accepted ${time(latestCommand.accepted_at)}`],
                    [
                      "Reservation",
                      latestCommand.reserved_at
                        ? `Reserved ${time(latestCommand.reserved_at)}`
                        : "No reservation",
                    ],
                    [
                      "Execution",
                      latestCommand.result
                        ? display(latestCommand.result.status)
                        : latestCommand.refusal_reason
                          ? display(latestCommand.refusal_reason)
                          : latestCommand.reserved_at
                            ? "Outcome unknown"
                            : "Not reported",
                    ],
                    [
                      "Measured effect",
                      latestCommand.effect
                        ? display(latestCommand.effect.status)
                        : "Awaiting evidence",
                    ],
                  ].map(([title, status], i) => (
                    <div key={title}>
                      <span
                        className={`node-icon ${["pink", "violet", "cyan", "teal"][i]}`}
                      >
                        <Icon
                          name={["send", "shield", "code", "Measurements"][i]!}
                        />
                      </span>
                      <h4>{title}</h4>
                      <p>{status}</p>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
          <h3>Speed command history</h3>
          {timeline.commands.length === 0 && <p>No speed requests recorded.</p>}
          {timeline.commands.map((command) => (
            <article
              className="operator-entry"
              key={command.command_id}
              id={`command-${command.command_id}`}
            >
              <h4>
                {command.request.parameters.value}{" "}
                {command.request.parameters.unit} speed cap
              </h4>
              <p>{command.request.reason}</p>
              <dl>
                <dt>Request state</dt>
                <dd>
                  {display(command.state)} · Accepted{" "}
                  {time(command.accepted_at)} · Deadline{" "}
                  {time(command.request.expires_at)}
                </dd>
                <dt>Handoff</dt>
                <dd>
                  {command.reserved_at
                    ? `Reserved at ${time(command.reserved_at)}`
                    : "No reservation recorded"}
                </dd>
                <dt>Execution</dt>
                <dd>
                  {command.refusal_reason
                    ? display(command.refusal_reason)
                    : command.result
                      ? `${display(command.result.status)}${command.recorded_at ? ` at ${time(command.recorded_at)}` : ""}`
                      : command.reserved_at
                        ? "Outcome unknown"
                        : "No execution reported"}
                </dd>
                <dt>Measured effect</dt>
                <dd>
                  {command.effect
                    ? `${display(command.effect.status)} · Peak ${command.effect.maximum_commanded_m_s.toFixed(2)} m/s`
                    : [
                          "canceled",
                          "superseded",
                          "expired",
                          "rejected_before_actuation",
                        ].includes(command.state)
                      ? "No effect assessment"
                      : "Awaiting measured evidence"}
                </dd>
                {command.changed_at && (
                  <>
                    <dt>Operator change</dt>
                    <dd>
                      {time(command.changed_at)}{" "}
                      {command.replacement_command_id && (
                        <a href={`#command-${command.replacement_command_id}`}>
                          View replacement
                        </a>
                      )}
                    </dd>
                  </>
                )}
              </dl>
            </article>
          ))}
          <h3>Mission intents</h3>
          {timeline.missions.length === 0 && (
            <p>No mission intents recorded.</p>
          )}
          {timeline.missions.map((mission) => (
            <article className="operator-entry" key={mission.intent.intent_id}>
              <h4>{mission.intent.station_id} · Intent recorded for review</h4>
              <p>
                {mission.intent.reason} · {time(mission.recorded_at)}
              </p>
              <small>Station catalog {mission.station_catalog_sha256}</small>
            </article>
          ))}
        </>
      )}
      {notice && <p role="status">{notice}</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  );
}
