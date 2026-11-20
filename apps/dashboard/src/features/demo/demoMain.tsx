import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  initialPlayer,
  createProjector,
  readCase,
  transition,
  csvCell,
  type Choice,
  type DemoCase,
} from "./demoModel.ts";
import { DashboardShell } from "../../app/DashboardShell.tsx";
import { Measurements } from "../measurements/Measurements.tsx";
import { recordedSnapshot } from "./demoAdapter.ts";
import {
  RecordedActivity,
  RecordedSystem,
  RecordedOverview,
  RecordedMap,
  RecordedTrend,
  RecordedEnergy,
  RecordedAdvisor,
  RecordedFleet,
  ChargingOffer,
  SignalPicker,
  arrivalLabel,
  num,
  workingDuration,
} from "./RecordedDashboard.tsx";
import { Icon, Logo } from "../../ui/Icon.tsx";
import { SectionHeading } from "../../ui/SectionHeading.tsx";
import type { Tab } from "../../app/presentation.ts";
import "../../ui/theme.css";
import "../../style.css";
import "../../ui/loading.css";
import "../forecast/route-energy.css";
import "../operations/operations-ux.css";
import "./demo.css";

const percent = (v: number) => `${num(v * 100)}%`;
function download(name: string, body: string, type: string) {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Story({ data }: { data: DemoCase }) {
  const [player, setPlayer] = useState(initialPlayer),
    [tab, setTab] = useState<Tab>("Overview");
  const [selected, setSelected] = useState<string[]>([]),
    [from, setFrom] = useState(0),
    [to, setTo] = useState(data.manifest.decision.at),
    [range, setRange] = useState([0, data.manifest.decision.at]);
  const [compare, setCompare] = useState(false),
    [muted, setMuted] = useState(false),
    [decisionOpen, setDecisionOpen] = useState(false),
    [aboutOpen, setAboutOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null),
    about = useRef<HTMLDialogElement>(null),
    returnFocus = useRef<HTMLElement | null>(null),
    audio = useRef<AudioContext | null>(null);
  const send = (a: Parameters<typeof transition>[1]) =>
    setPlayer((p) => transition(p, a, data));
  const project = useMemo(() => createProjector(data), [data]);
  const view = project(player);
  const waiting = player.phase === "decision",
    ended = player.phase === "finished",
    ready = player.phase === "ready",
    d = data.manifest.decision;
  const charging = ["charging", "target_reached"].includes(
    view.transfer?.state ?? "",
  );
  const summary = player.choice ? data[player.choice].summary! : null;
  const end = summary?.duration ?? d.at;
  const seek = (t: number) => send({ type: "seek", t });
  function navigate(next: Tab) {
    if (next === "History") {
      setSelected([]);
      setFrom(0);
      setTo(player.t);
      setRange([0, player.t]);
    }
    setTab(next);
  }
  function history(ids: string[], start = 0, end = player.t) {
    setSelected(ids);
    setFrom(start);
    setTo(end);
    setRange([start, end]);
    setTab("History");
  }
  function openMap() {
    setDecisionOpen(false);
    navigate("Map");
  }
  function review() {
    if (waiting) setDecisionOpen(true);
    else navigate("Operator");
  }
  function choose(choice: Choice) {
    setDecisionOpen(false);
    send({ type: "choose", choice });
  }
  function unlockSound() {
    try {
      audio.current ??= new AudioContext();
      void audio.current.resume().catch(() => undefined);
    } catch {
      /* Optional sound; playback is independent. */
    }
  }
  function start() {
    unlockSound();
    send({ type: "start" });
  }
  function exit() {
    setDecisionOpen(false);
    setAboutOpen(false);
    setCompare(false);
    setTab("Overview");
    send({ type: "exit" });
  }

  useEffect(() => {
    let last = performance.now();
    const timer = setInterval(() => {
      const now = performance.now(),
        delta = (now - last) / 1000;
      last = now;
      setPlayer((p) =>
        transition(p, { type: "tick", delta, hidden: document.hidden }, data),
      );
    }, 100);
    const visible = () => {
      last = performance.now();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [data]);
  useEffect(() => {
    setDecisionOpen(waiting);
    if (waiting && !muted && audio.current?.state === "running") {
      const osc = audio.current.createOscillator(),
        gain = audio.current.createGain();
      osc.connect(gain);
      gain.connect(audio.current.destination);
      osc.frequency.value = 660;
      gain.gain.setValueAtTime(0.06, audio.current.currentTime);
      gain.gain.exponentialRampToValueAtTime(
        0.001,
        audio.current.currentTime + 0.3,
      );
      osc.start();
      osc.stop(audio.current.currentTime + 0.3);
    }
    // Sound and modal appear once on entering a decision, not on reading updates.
  }, [waiting, player.generation]);
  useEffect(() => {
    const node = dialog.current;
    if (decisionOpen && !node?.open) {
      returnFocus.current = document.activeElement as HTMLElement;
      node?.showModal();
    }
    if (!decisionOpen && node?.open) {
      node.close();
      returnFocus.current?.focus();
    }
  }, [decisionOpen]);
  useEffect(() => {
    if (aboutOpen && !about.current?.open) about.current?.showModal();
    if (!aboutOpen && about.current?.open) about.current.close();
  }, [aboutOpen]);
  useEffect(() => {
    setSelected([]);
    setFrom(0);
    setTo(d.at);
    setRange([0, d.at]);
    setCompare(false);
  }, [player.generation, d.at]);
  useEffect(
    () => () => {
      void audio.current?.close();
    },
    [],
  );

  function exportHistory(csv: boolean) {
    const indices = selected.map((id) =>
      data.catalog.findIndex((s) => s.id === id),
    );
    const rows = view.frames.filter(
      (f) => f[0] >= range[0]! && f[0] <= Math.min(range[1]!, player.t),
    );
    if (csv) {
      const header = [
        "working_seconds",
        ...selected.flatMap((id, i) => [
          `${id} (${data.catalog[indices[i]!]!.unit})`,
          `${id} quality`,
        ]),
      ];
      download(
        "drivetwin-history.csv",
        [
          header,
          ...rows.map((f) => [
            f[0],
            ...indices.flatMap((i) => [f[1][i] ?? "", f[2]?.[i] ?? "good"]),
          ]),
        ]
          .map((row) => row.map(csvCell).join(","))
          .join("\n"),
        "text/csv;charset=utf-8",
      );
    } else
      download(
        "drivetwin-history.json",
        JSON.stringify(
          {
            basis: data.manifest.basis,
            choice: player.choice,
            signals: indices.map((i) => data.catalog[i]),
            observations: rows.map((f) => ({
              working_s: f[0],
              values: indices.map((i) => f[1][i]),
              quality: indices.map((i) => f[2]?.[i] ?? "good"),
            })),
          },
          null,
          2,
        ),
        "application/json",
      );
  }
  const toolbar = (
    <section className="assetbar" aria-label="Vehicle and playback controls">
      <span className="asset-icon">
        <Icon name="Fleet" />
      </span>
      <label>
        Vehicle
        <select aria-label="Vehicle" defaultValue="bus-001">
          <option>bus-001</option>
        </select>
      </label>
      <div className="asset-meta">
        <strong title={data.manifest.title}>{data.manifest.title}</strong>
        <span>Electric vehicle digital twin</span>
      </div>
      {arrivalLabel(view, player.t) && !ready && (
        <span className="trip-arrival-chip">
          <Icon name="clock" size={16} />
          Arrival in {arrivalLabel(view, player.t)}
        </span>
      )}

      {!ready && (
        <button
          className="secondary"
          aria-label={player.playing ? "Pause playback" : "Resume playback"}
          disabled={waiting || ended}
          onClick={() => send({ type: "toggle" })}
        >
          <Icon name={player.playing ? "pause" : "play"} size={17} />
          {waiting
            ? "Paused"
            : ended
              ? "Finished"
              : player.playing
                ? "Pause"
                : "Resume"}
        </button>
      )}
      {!ready && (
        <button
          className={`secondary charge-shortcut ${charging ? "is-charging" : ""}`}
          onClick={review}
        >
          <Icon name={charging ? "battery" : "Energy"} size={18} />
          {waiting
            ? "Review charging stop"
            : charging
              ? "Vehicle charging"
              : "Charging status"}
        </button>
      )}
      <button
        className="icon-button trip-sound"
        aria-label={
          muted ? "Enable notification sounds" : "Mute notification sounds"
        }
        aria-pressed={!muted}
        title={
          muted ? "Enable notification sounds" : "Mute notification sounds"
        }
        onClick={() => {
          if (muted) unlockSound();
          setMuted((v) => !v);
        }}
      >
        <Icon name={muted ? "sound-off" : "sound-on"} size={19} />
      </button>
    </section>
  );

  return (
    <div className="recorded-mode">
      <DashboardShell
        view={tab}
        navigate={navigate}
        charging
        toolbar={toolbar}
        disconnect={exit}
        disconnectLabel="Exit demo"
        description={
          tab === "Overview"
            ? "Recorded readings and vehicle condition"
            : undefined
        }
        sidebarFooter={
          <>
            <span className="recorded-sidebar-note">
              <Icon name="shield" size={19} />
              Recorded demo
            </span>
            <button
              className="recorded-about-link"
              onClick={() => setAboutOpen(true)}
            >
              About this demonstration
            </button>
          </>
        }
      >
        {ready && (
          <section className="panel recorded-start">
            <SectionHeading
              eyebrow="RECORDED TRIP"
              icon="Map"
              tone="teal"
              title={data.manifest.title}
            />
            <p>
              Explore the dashboard. After {d.at} working seconds, playback
              pauses for your charging decision.
            </p>
            <div className="recorded-start-bottom">
              <dl>
                <div>
                  <dt>Starting charge</dt>
                  <dd>{percent(data.manifest.initialSoc)}</dd>
                </div>
                <div>
                  <dt>Battery reserve</dt>
                  <dd>{percent(data.manifest.reserveSoc)}</dd>
                </div>
                <div>
                  <dt>Introduction</dt>
                  <dd>{d.at} s at 1×</dd>
                </div>
              </dl>
              <button className="primary" onClick={start}>
                <Icon name="play" />
                Start recorded trip
              </button>
            </div>
          </section>
        )}
        {ended && (
          <section className="panel recorded-results">
            <div className="panel-head">
              <SectionHeading
                icon="check"
                tone="teal"
                eyebrow="RECORDED OUTCOME"
                title="Destination reached"
              />
              <button
                className="secondary"
                onClick={() => send({ type: "other" })}
              >
                Review alternative decision
              </button>
            </div>
            <div className="recorded-result-metrics">
              <div>
                <span>Arrival charge</span>
                <strong>{percent(summary!.arrivalSoc)}</strong>
              </div>
              <div>
                <span>Lowest charge</span>
                <strong>{percent(summary!.minimumSoc)}</strong>
              </div>
              <div>
                <span>Measured energy delivered</span>
                <strong>{num(summary!.deliveredKwh)} kWh</strong>
              </div>
              <div>
                <span>Trip working time</span>
                <strong>{workingDuration(summary!.duration)}</strong>
              </div>
            </div>
            <div className="recorded-result-actions">
              <button onClick={() => setCompare((v) => !v)}>
                {compare ? "Hide outcome comparison" : "Compare both outcomes"}
              </button>
              <button onClick={() => send({ type: "restart" })}>
                Restart recording
              </button>
            </div>
            {compare && (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Recorded outcome</th>
                      <th>Arrival charge</th>
                      <th>Lowest charge</th>
                      <th>Energy delivered</th>
                      <th>Working time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(["charge", "continue"] as const).map((choice) => {
                      const s = data[choice].summary!;
                      return (
                        <tr key={choice}>
                          <th>
                            {choice === "charge"
                              ? "Charging stop approved"
                              : "Continued without charging"}
                          </th>
                          <td>{percent(s.arrivalSoc)}</td>
                          <td>{percent(s.minimumSoc)}</td>
                          <td>{num(s.deliveredKwh)} kWh</td>
                          <td>{workingDuration(s.duration)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        )}
        {tab === "Overview" && (
          <RecordedOverview
            key={player.generation}
            data={data}
            player={player}
            view={view}
            navigate={navigate}
          />
        )}
        {tab === "Map" && <RecordedMap data={data} view={view} />}
        {tab === "Measurements" && (
          <Measurements
            snap={recordedSnapshot(data, player, view)}
            connected
            recorded
            openHistory={(id) => history([id])}
          />
        )}
        {tab === "Fleet" && (
          <RecordedFleet
            data={data}
            player={player}
            view={view}
            navigate={navigate}
          />
        )}
        {tab === "Energy" && (
          <RecordedEnergy
            key={player.generation}
            data={data}
            player={player}
            view={view}
            navigate={navigate}
            history={(from, to) =>
              history(
                ["battery.pack.soc", "battery.pack.energy", "motion.speed"],
                from,
                to,
              )
            }
          />
        )}
        {tab === "Operator" && (
          <RecordedAdvisor
            data={data}
            player={player}
            view={view}
            review={review}
            openMap={openMap}
          />
        )}
        {tab === "History" && (
          <div className="history-explorer">
            <section className="panel history-query">
              <div className="panel-head">
                <SectionHeading
                  eyebrow="RECORDED READINGS"
                  title="Choose a recorded interval"
                  icon="History"
                  tone="indigo"
                />
              </div>
              <div className="query-range">
                <label>
                  From (working s)
                  <input
                    aria-label="From working seconds"
                    type="number"
                    min={0}
                    value={from}
                    onChange={(e) => setFrom(Number(e.target.value))}
                  />
                </label>
                <label>
                  To (working s)
                  <input
                    aria-label="To working seconds"
                    type="number"
                    min={0}
                    value={to}
                    onChange={(e) => setTo(Number(e.target.value))}
                  />
                </label>
                <button onClick={() => setTo(player.t)}>Use latest time</button>
                <button
                  className="primary"
                  disabled={
                    !Number.isFinite(from) ||
                    !Number.isFinite(to) ||
                    from < 0 ||
                    to < from
                  }
                  onClick={() => setRange([from, Math.min(to, player.t)])}
                >
                  Apply historical query
                </button>
              </div>
              <SignalPicker
                items={data.catalog}
                selected={selected}
                onSelect={setSelected}
              />
              <details className="recorded-history-export">
                <summary>
                  <Icon name="download" size={15} /> Export values
                </summary>
                <p className="fine">
                  Selected signals and interval, through the playback position.
                </p>
                <div className="query-actions">
                  <button
                    disabled={!selected.length}
                    onClick={() => exportHistory(true)}
                  >
                    Download CSV
                  </button>
                  <button
                    disabled={!selected.length}
                    onClick={() => exportHistory(false)}
                  >
                    Download JSON
                  </button>
                </div>
              </details>
              <div className="history-presets">
                <span className="fine">Quick selections</span>
                <button
                  onClick={() =>
                    setSelected([
                      "battery.pack.soc",
                      "battery.pack.energy",
                      "motion.speed",
                    ])
                  }
                >
                  Energy and motion
                </button>
                <button
                  onClick={() => setSelected(data.catalog.map((s) => s.id))}
                >
                  All signals
                </button>
              </div>
            </section>
            {selected.length ? (
              selected.map((id) => (
                <RecordedTrend
                  key={`${player.generation}-${id}`}
                  data={data}
                  frames={view.frames}
                  id={id}
                  from={range[0]!}
                  to={Math.min(range[1]!, player.t)}
                  cursor={player.t}
                  seek={player.choice ? seek : undefined}
                />
              ))
            ) : (
              <section className="panel empty">
                <SectionHeading
                  title="Choose signals to explore"
                  icon="History"
                  tone="indigo"
                />
                <p>
                  Each selected signal adds a chart with the shared working-time
                  cursor.
                </p>
              </section>
            )}
          </div>
        )}
        {tab === "Activity" && (
          <RecordedActivity
            key={player.generation}
            view={view}
            seek={player.choice ? seek : undefined}
          />
        )}
        {tab === "System" && <RecordedSystem data={data} view={view} />}
      </DashboardShell>
      {!ready && (
        <footer className="recorded-player" aria-label="Recording playback">
          <div className="recorded-player-meta">
            <Icon name="History" size={18} />
            <strong>{num(player.t, 1)} s</strong>
            <span>/ {num(end, 1)} working s</span>
          </div>
          <input
            aria-label="Recorded trip playhead"
            type="range"
            min={0}
            max={end}
            step={0.1}
            value={player.t}
            disabled={!player.choice}
            onInput={(e) => seek(Number(e.currentTarget.value))}
            style={
              {
                "--progress": `${(player.t / end) * 100}%`,
              } as React.CSSProperties
            }
          />
          <span className="recorded-player-phase" role="status">
            {waiting ? (
              <button
                className="text-button"
                onClick={() => setDecisionOpen(true)}
              >
                Review decision
              </button>
            ) : player.phase === "intro" ? (
              `Decision in ${Math.max(0, d.at - Math.round(player.t))} s`
            ) : ended ? (
              "Playback finished"
            ) : player.choice === "charge" ? (
              "Charging approved"
            ) : (
              "Without charging"
            )}
          </span>
          <label>
            <span className="sr-only">Playback speed</span>
            <select
              aria-label="Playback speed"
              disabled={!player.choice}
              value={player.speed}
              onChange={(e) =>
                send({ type: "speed", speed: Number(e.target.value) })
              }
            >
              {[0.5, 1, 2, 5, 10, 20].map((v) => (
                <option key={v} value={v}>
                  {v}×
                </option>
              ))}
            </select>
          </label>
          <button
            title="Restart the recording and clear the current decision"
            onClick={() => send({ type: "restart" })}
          >
            <Icon name="reset" size={17} />
            Restart
          </button>
        </footer>
      )}
      <dialog
        ref={dialog}
        className="charging-alert recorded-decision"
        aria-labelledby="recorded-decision-title"
        onCancel={(e) => {
          e.preventDefault();
          setDecisionOpen(false);
        }}
      >
        <div className="charging-alert-header">
          <div>
            <Icon name="Energy" />
            <h2 id="recorded-decision-title">Charging stop recommended</h2>
          </div>
          <button onClick={() => setDecisionOpen(false)}>Hide alert</button>
        </div>
        <p className="charging-alert-message">
          {view.forecast?.summary
            ? `Predicted lowest charge is ${percent(view.forecast.summary.minimum)}, below the ${percent(data.manifest.reserveSoc)} reserve.`
            : "The recorded forecast predicts a battery reserve shortfall."}
        </p>
        <ChargingOffer data={data} t={player.t} openMap={openMap} />
        <div className="recorded-decision-review">
          <p>
            <Icon name="pause" size={15} /> Playback paused for your decision
          </p>
          <div className="charge-primary-actions">
            <button className="primary" onClick={() => choose("charge")}>
              <Icon name="Energy" size={17} /> Approve charging stop
            </button>
            <button className="secondary" onClick={() => choose("continue")}>
              Continue without charging
            </button>
          </div>
        </div>
        <details className="charge-estimates" data-dismiss-outside>
          <summary>Charging and trip estimates</summary>
          <div className="recorded-estimates">
            <div>
              <Icon name="Map" />
              <span>Predicted destination charge</span>
              <strong>{percent(d.arrivalSocWithStop)}</strong>
            </div>
            <div>
              <Icon name="clock" />
              <span>Wait and setup</span>
              <strong>{workingDuration(d.waitSeconds)}</strong>
            </div>
            <div>
              <Icon name="battery" />
              <span>Estimated charging energy</span>
              <strong>{num(d.energyKwh)} kWh</strong>
            </div>
          </div>
        </details>
        <div className="charging-alert-actions">
          <button
            onClick={() => {
              setDecisionOpen(false);
              navigate("Energy");
            }}
          >
            View Energy Forecast
          </button>
        </div>
      </dialog>
      <dialog
        ref={about}
        className="recorded-about"
        aria-labelledby="recorded-about-title"
        onCancel={(e) => {
          e.preventDefault();
          setAboutOpen(false);
        }}
      >
        <div className="panel-head">
          <SectionHeading
            title={
              <span id="recorded-about-title">About this demonstration</span>
            }
            icon="shield"
            tone="teal"
          />
          <button onClick={() => setAboutOpen(false)}>Close</button>
        </div>
        <p>
          This recorded demo uses the DriveTwin dashboard with a saved trip
          through Montréal. At {d.at} working seconds, playback pauses for a
          charging decision. Each choice opens a separately recorded outcome.
        </p>
        <p>
          Roads and elevation represent real geography. Vehicle and battery
          readings were generated in simulation; charging bays and availability
          belong to the recorded scenario. This demo sends no live vehicle
          commands and runs no simulator.
        </p>
        <p>
          Forecasts appear when they became available in the recording. Their
          input cutoff can be earlier. Service-health data that was not captured
          is labeled as not recorded.
        </p>
        <details>
          <summary>Recording provenance and notices</summary>
          <p>{data.manifest.basis}</p>
          <ul>
            {data.manifest.attribution.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <p className="fine">
            Case {data.manifest.id} · {data.manifest.version}
          </p>
          <code className="recorded-fingerprint">
            {data.manifest.evidence.prefixSha256}
          </code>
        </details>
      </dialog>
    </div>
  );
}

function App() {
  const [data, setData] = useState<DemoCase | null>(null),
    [error, setError] = useState(""),
    [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setError("");
    setData(null);
    void Promise.all([
      readCase(
        new URL("./cases/montreal/manifest.json", document.baseURI),
        abort.signal,
      ),
      import("../map/richBackdrop.ts"),
    ])
      .then(([loaded]) => {
        if (!abort.signal.aborted) setData(loaded);
      })
      .catch((e) => {
        if (!abort.signal.aborted)
          setError(e instanceof Error ? e.message : "Case could not be loaded");
      });
    return () => abort.abort();
  }, [attempt]);
  if (!data)
    return (
      <div className="recorded-loading">
        <a className="brand">
          <Logo />
          <span>DriveTwin</span>
        </a>
        {error ? (
          <>
            <Icon name="alert" size={36} />
            <h1>The recorded trip could not be opened</h1>
            <p>{error}. No playback has started.</p>
            <button
              className="primary"
              onClick={() => setAttempt((v) => v + 1)}
            >
              Try again
            </button>
          </>
        ) : (
          <>
            <span className="loading-spinner" />
            <h1>Preparing your trip</h1>
            <p>Loading the recording and checking both outcomes.</p>
          </>
        )}
      </div>
    );
  return <Story data={data} />;
}
createRoot(document.getElementById("root")!).render(<App />);
