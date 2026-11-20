import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Icon, Logo } from "../ui/Icon.tsx";
import { Overview, Fleet, Measurements } from "./Readings.tsx";
import { History } from "./History.tsx";
import { Energy } from "./Energy.tsx";
import { Activity, Operator, Replay, System } from "./Operations.tsx";
import { PreviewMap } from "./PreviewMap.tsx";
import { initialEvents, modes, tabs, vehicleName } from "./data.ts";
import type { EventRecord, Mode, Tab, Vehicle } from "./data.ts";
import { isStale, Status } from "./ui.tsx";
import type { PreviewContext } from "./ui.tsx";
import "../ui/theme.css";
import fontLicenseUrl from "../ui/assets/Inter-LICENSE.txt?url";

const descriptions: Record<Tab, string> = {
  Overview: "Live readings and vehicle condition",
  Fleet: "Compare readings across selected vehicles",
  Map: "Location, route, and nearby charging options",
  Measurements: "Every registered reading, with its quality and source",
  History: "Explore recorded readings over a selected time window",
  Energy: "Route forecasts and station approach estimates",
  Activity: "Source events and data-quality changes",
  System: "Delivery, storage, and connection status",
  Operator: "Vehicle controls and independently observed outcomes",
};
const titles: Record<Tab, string> = {
  Overview: "Vehicle overview",
  Fleet: "Fleet measurements",
  Map: "Route and location",
  Measurements: "All measurements",
  History: "History",
  Energy: "Energy forecast",
  Activity: "Activity",
  System: "System health",
  Operator: "Vehicle operator",
};

function App() {
  const [tab, setTab] = useState<Tab>("Overview"),
    [vehicle, setVehicle] = useState<Vehicle>("bus-001"),
    [mode, setMode] = useState<Mode>("Running");
  const [time, setTime] = useState(120),
    [age, setAge] = useState(0.8),
    [historySignal, setHistorySignal] = useState("battery.pack.soc"),
    [menu, setMenu] = useState(false),
    [replay, setReplay] = useState(false),
    [resync, setResync] = useState(false);
  const [events, setEvents] = useState(initialEvents),
    [browserEvents, setBrowserEvents] = useState([
      {
        time: new Date().toLocaleTimeString("en-US", { hour12: false }),
        label: "Connected to sample data",
      },
    ]);
  const eventId = useRef(10),
    recoveryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
      undefined,
    );
  useEffect(() => {
    const timer = setInterval(() => {
      if (["Running", "Invalid reading", "Out of domain"].includes(mode)) {
        setTime((t) => t + 1);
        setAge(0.8);
      } else setAge((a) => a + 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [mode]);
  useEffect(() => () => clearTimeout(recoveryTimer.current), []);
  useEffect(() => {
    if (!replay) return;
    const previous = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setReplay(false);
      if (e.key === "Tab") {
        const elements = Array.from(
          document.querySelectorAll<HTMLElement>(
            '.replay-overlay button:not(:disabled), .replay-overlay input, .replay-overlay select, .replay-overlay summary, .replay-overlay a[href], .replay-overlay [tabindex]:not([tabindex="-1"])',
          ),
        ).filter((el) => el.getClientRects().length > 0);
        const first = elements[0],
          last = elements.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [replay]);
  const navigate = (next: Tab) => {
    setTab(next);
    setMenu(false);
    window.scrollTo({ top: 0 });
  };
  const changeMode = (next: Mode) => {
    if (next === mode) return;
    if (next === "Running") {
      setAge(0.8);
      if (mode === "Disconnected") {
        setResync(true);
        clearTimeout(recoveryTimer.current);
        recoveryTimer.current = setTimeout(() => setResync(false), 5000);
      }
    } else if (next === "Stale") setAge(12);
    else if (next === "Missing data") setAge(0);
    const record: EventRecord = {
      id: eventId.current++,
      t: time,
      title:
        next === "Running"
          ? "Source resumed"
          : next === "Paused"
            ? "Source paused"
            : next === "Invalid reading"
              ? "Invalid reading recorded"
              : `Preview state changed to ${next.toLowerCase()}`,
      detail: `The selected preview state is ${next.toLowerCase()}.`,
      tone:
        next === "Running" ? "teal" : next === "Paused" ? "violet" : "amber",
      type: ["Invalid reading", "Missing data"].includes(next)
        ? "Quality"
        : "Source",
    };
    if (next !== "Disconnected" && mode !== "Disconnected")
      setEvents((previous) => [record, ...previous].slice(0, 100));
    if (next === "Disconnected" || mode === "Disconnected")
      setBrowserEvents((previous) =>
        [
          {
            time: new Date().toLocaleTimeString("en-US", { hour12: false }),
            label:
              next === "Disconnected"
                ? "Disconnected · last readings retained"
                : "Reconnected · fresh snapshot received",
          },
          ...previous,
        ].slice(0, 20),
      );
    setMode(next);
  };
  const c: PreviewContext = {
    time,
    age,
    mode,
    vehicle,
    historySignal,
    setTab: navigate,
    openHistory: (id) => {
      setHistorySignal(id);
      navigate("History");
    },
  };
  const changeVehicle = (next: Vehicle) => {
    setVehicle(next);
    setTime(120);
    setAge(0.8);
    setMode("Running");
    setEvents(initialEvents);
    setResync(false);
    setReplay(false);
  };
  return (
    <div className="drivetwin-preview">
      <aside inert={replay} className={`sidebar ${menu ? "open" : ""}`}>
        <a
          className="brand"
          href="#overview"
          onClick={(e) => {
            e.preventDefault();
            navigate("Overview");
          }}
        >
          <Logo />
          <span>DriveTwin</span>
        </a>
        <nav aria-label="Main navigation">
          {tabs.map((name) => (
            <button
              key={name}
              className={tab === name ? "active" : ""}
              aria-current={tab === name ? "page" : undefined}
              onClick={() => navigate(name)}
            >
              <Icon name={name} size={21} />
              <span>{name}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button onClick={() => setReplay(true)}>
            <Icon name="History" size={18} />
            Open replay
            <Icon name="external" size={15} />
          </button>
          <span className="preview-label">
            <span />
            Interactive preview
          </span>
        </div>
      </aside>
      {menu && (
        <button
          className="nav-scrim"
          aria-label="Close navigation"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="workspace" inert={replay}>
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            aria-label="Open navigation"
            onClick={() => setMenu(!menu)}
          >
            <Icon name="menu" />
          </button>
          <label className="vehicle-select">
            <Icon name="Fleet" size={18} />
            <select
              aria-label="Selected vehicle"
              value={vehicle}
              onChange={(e) => changeVehicle(e.target.value as Vehicle)}
            >
              <option value="bus-001">Bus 001</option>
              <option value="bus-002">Bus 002</option>
            </select>
          </label>
          <span className="location-label">
            Montréal <span>·</span> Vehicle monitoring
          </span>
          <div className="header-status">
            <Status
              label={mode === "Disconnected" ? "Disconnected" : "Connected"}
              tone={mode === "Disconnected" ? "amber" : "teal"}
            />
            <span className="sample-label">Sample data</span>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <h1>{titles[tab]}</h1>
              <p>{descriptions[tab]}</p>
            </div>
            <label className="state-control">
              <span>Preview state</span>
              <select
                aria-label="Preview state"
                value={mode}
                onChange={(e) => changeMode(e.target.value as Mode)}
              >
                {modes.map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
            </label>
          </div>
          {resync && (
            <div className="notice teal" role="status">
              <Icon name="connection" />
              Reconnected. A fresh vehicle snapshot has been received.
            </div>
          )}
          {isStale(c) && (
            <div className="notice amber" role="status">
              <Icon name="clock" />
              {mode === "Disconnected"
                ? "The browser is disconnected. Last received readings remain visible."
                : mode === "Paused"
                  ? "The source is paused. Reading ages continue to increase."
                  : "Readings are stale. Last received values remain visible."}
            </div>
          )}
          <div key={vehicle} className="page-content">
            {tab === "Overview" && <Overview c={c} />}
            {tab === "Fleet" && <Fleet c={c} />}
            {tab === "Map" && (
              <PreviewMap
                time={time}
                vehicle={vehicle}
                stale={isStale(c)}
                available={mode !== "Missing data"}
              />
            )}
            {tab === "Measurements" && <Measurements c={c} />}
            {tab === "History" && <History c={c} />}
            {tab === "Energy" && <Energy c={c} />}
            {tab === "Activity" && (
              <Activity c={c} events={events} browserEvents={browserEvents} />
            )}
            {tab === "System" && <System c={c} />}
            <div hidden={tab !== "Operator"} className="operator-page">
              <Operator c={c} />
            </div>
          </div>
          <footer className="workspace-footer">
            <span>
              DriveTwin{" "}
              <a href={fontLicenseUrl} target="_blank" rel="noreferrer">
                Font license
              </a>
            </span>
            <span>
              {vehicleName(vehicle)} · 20 registered measurements · Sample
              recording
            </span>
          </footer>
        </main>
      </div>
      {replay && <Replay vehicle={vehicle} onClose={() => setReplay(false)} />}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
