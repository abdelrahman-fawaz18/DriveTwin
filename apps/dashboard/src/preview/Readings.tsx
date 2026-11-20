import { Fragment, useState } from "react";
import { Chart, Bars } from "../ui/Chart.tsx";
import { Icon } from "../ui/Icon.tsx";
import {
  clock,
  groups,
  number,
  observationTime,
  palette,
  sampleValue,
  series,
  signalById,
  signals,
  vehicleName,
} from "./data.ts";
import type { Vehicle } from "./data.ts";
import { currentValue, Empty, isStale, Metric, Panel, Status } from "./ui.tsx";
import type { PreviewContext } from "./ui.tsx";

export function Overview({ c }: { c: PreviewContext }) {
  const [trend, setTrend] = useState("battery.pack.soc"),
    [window, setWindow] = useState(60);
  const selected = signalById(trend),
    charge = trend === "battery.pack.soc";
  const points = series(
    trend,
    Math.max(0, c.time - window),
    c.time,
    c.vehicle,
  ).map((p) => ({
    ...p,
    value:
      c.mode === "Invalid reading" &&
      trend === "traction.motor.temperature" &&
      p.t === c.time
        ? null
        : charge
          ? p.value! * 100
          : p.value,
  }));
  const value = (id: string, scale = 1, offset = 0) => {
    const v = currentValue(id, c);
    return v == null ? null : v * scale + offset;
  };
  const stale = isStale(c),
    available = c.mode !== "Missing data";
  return (
    <>
      <div className="metrics-row">
        <Metric
          label="Battery charge"
          value={value("battery.pack.soc", 100)}
          unit="%"
          icon="battery"
          tone="pink"
          caption={
            available
              ? `Updated ${number(c.age, 1)} s ago`
              : "Awaiting readings"
          }
        />
        <Metric
          label="Speed"
          value={value("motion.speed", 3.6)}
          unit="km/h"
          icon="Overview"
          tone="violet"
          caption={stale ? "Last observed value" : "Current vehicle speed"}
        />
        <Metric
          label="Stored energy"
          value={value("battery.pack.energy", 1 / 3600000)}
          unit="kWh"
          icon="layers"
          tone="cyan"
          caption="Usable battery energy"
        />
        <Metric
          label="Motor temperature"
          value={value("traction.motor.temperature", 1, -273.15)}
          unit="°C"
          icon="temperature"
          tone="amber"
          caption={
            c.mode === "Invalid reading" ? "Invalid reading" : "Traction motor"
          }
        />
      </div>
      <div className="overview-grid">
        <Panel
          title="Live trend"
          action={
            <div className="inline-controls">
              <select
                aria-label="Live trend signal"
                value={trend}
                onChange={(e) => setTrend(e.target.value)}
              >
                {signals.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
              <select
                aria-label="Live trend window"
                value={window}
                onChange={(e) => setWindow(Number(e.target.value))}
              >
                <option value={60}>Last 60 s</option>
                <option value={120}>Last 2 min</option>
                <option value={300}>Last 5 min</option>
              </select>
            </div>
          }
        >
          <div className="chart-heading">
            <span
              className="legend-dot"
              style={{ background: selected.color }}
            />
            {selected.label} ({charge ? "%" : selected.unit})
            <span className="muted push-right">
              {stale ? "Last received window" : "Updated every second"}
            </span>
          </div>
          {available ? (
            <Chart
              points={points}
              color={selected.color}
              unit={charge ? "%" : selected.unit}
              height={300}
              label={`${selected.label} over working time`}
            />
          ) : (
            <Empty
              title="Awaiting readings"
              text="The selected vehicle has no current measurements."
            />
          )}
        </Panel>
        <aside className="stack">
          <Panel title="Source state" className="gradient charcoal">
            <div className="source-value">
              <Icon name={c.mode === "Paused" ? "pause" : "play"} size={26} />
              {c.mode === "Paused"
                ? "Paused"
                : available
                  ? "Running"
                  : "Unknown"}
            </div>
            <dl className="key-values">
              <dt>Working time</dt>
              <dd>{clock(c.time)}</dd>
              <dt>Reading age</dt>
              <dd>{number(c.age, 1)} s</dd>
              <dt>Connection</dt>
              <dd>
                {c.mode === "Disconnected" ? "Disconnected" : "Connected"}
              </dd>
            </dl>
          </Panel>
          <Panel title="Measurement coverage">
            <p className="muted">
              20 registered · {available && !stale ? 20 : 0} fresh ·{" "}
              {available && stale ? 20 : 0} stale
            </p>
            <div className="coverage-strip">
              {signals.map((s) => (
                <span
                  key={s.id}
                  className={!available ? "unknown" : stale ? "old" : "fresh"}
                  title={s.label}
                />
              ))}
            </div>
            <div className="coverage-caption">
              <span>
                {c.mode === "Invalid reading"
                  ? "19 good · 1 invalid"
                  : available
                    ? "20 good readings"
                    : "20 awaiting readings"}
              </span>
              <Icon name="check" size={16} />
            </div>
          </Panel>
        </aside>
      </div>
      <div className="quick-links">
        {(
          [
            {
              tab: "Measurements",
              label: "Explore measurements",
              icon: "Measurements",
            },
            { tab: "History", label: "Open history", icon: "History" },
            { tab: "Map", label: "View route", icon: "Map" },
          ] as const
        ).map((item) => (
          <button key={item.tab} onClick={() => c.setTab(item.tab)}>
            <Icon name={item.icon} />
            {item.label}
            <Icon name="arrow" size={18} />
          </button>
        ))}
      </div>
    </>
  );
}

export function Measurements({ c }: { c: PreviewContext }) {
  const [search, setSearch] = useState(""),
    [group, setGroup] = useState("All"),
    [quality, setQuality] = useState("All"),
    [freshness, setFreshness] = useState("All"),
    [detail, setDetail] = useState<string | null>(null);
  const stale = isStale(c);
  const visible = signals.filter((s) => {
    const good = currentValue(s.id, c) != null;
    return (
      (group === "All" ||
        groups.find((g) => g.name === group)?.ids.includes(s.id)) &&
      `${s.label} ${s.id} ${s.unit} ${s.component} ${s.source}`
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (quality === "All" || (quality === "Good" ? good : !good)) &&
      (freshness === "All" || (freshness === "Stale" ? stale : !stale))
    );
  });
  return (
    <>
      <div className="filter-bar">
        <label className="search-field">
          <Icon name="search" />
          <input
            aria-label="Search measurements"
            placeholder="Search measurements, units, components…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <label>
          Component
          <select value={group} onChange={(e) => setGroup(e.target.value)}>
            <option>All</option>
            {groups.map((g) => (
              <option key={g.name}>{g.name}</option>
            ))}
          </select>
        </label>
        <label>
          Quality
          <select value={quality} onChange={(e) => setQuality(e.target.value)}>
            <option>All</option>
            <option>Good</option>
            <option>Unavailable</option>
          </select>
        </label>
        <label>
          Freshness
          <select
            value={freshness}
            onChange={(e) => setFreshness(e.target.value)}
          >
            <option>All</option>
            <option>Fresh</option>
            <option>Stale</option>
          </select>
        </label>
      </div>
      <Panel className="measurement-panel">
        <div className="table-summary">
          <span>
            {visible.length} of {signals.length} measurements
          </span>
          <span>Stored units · one-second cadence</span>
        </div>
        <div className="table-scroll">
          <table className="measurements-table">
            <thead>
              <tr>
                <th>Signal / component</th>
                <th>Value</th>
                <th>Unit</th>
                <th>Quality / freshness</th>
                <th>Age</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => {
                const rows = visible.filter((s) => g.ids.includes(s.id));
                return (
                  rows.length > 0 && (
                    <Fragment key={g.name}>
                      <tr className={`group-row ${g.tone}`}>
                        <th colSpan={6}>
                          {g.name}
                          <span>{rows.length}</span>
                        </th>
                      </tr>
                      {rows.map((s) => (
                        <Fragment key={s.id}>
                          <tr>
                            <td>
                              <strong>{s.label}</strong>
                              <small>{s.component}</small>
                            </td>
                            <td className="numeric">
                              {number(
                                currentValue(s.id, c),
                                s.id === "battery.pack.soc" ? 4 : 3,
                              )}
                            </td>
                            <td>{s.unit}</td>
                            <td>
                              <Status
                                label={
                                  currentValue(s.id, c) == null
                                    ? c.mode === "Invalid reading"
                                      ? "Invalid"
                                      : "Unavailable"
                                    : "Good"
                                }
                                tone={
                                  currentValue(s.id, c) == null
                                    ? "amber"
                                    : "teal"
                                }
                              />
                              {stale && <Status label="Stale" tone="amber" />}
                            </td>
                            <td>
                              {c.mode === "Missing data"
                                ? "—"
                                : `${number(c.age, 1)} s`}
                            </td>
                            <td>
                              <div className="row-actions">
                                <button
                                  className="text-button"
                                  onClick={() => c.openHistory(s.id)}
                                >
                                  History <Icon name="arrow" size={14} />
                                </button>
                                <button
                                  className="icon-button"
                                  aria-label={`${detail === s.id ? "Hide" : "Show"} ${s.label} metadata`}
                                  aria-expanded={detail === s.id}
                                  onClick={() =>
                                    setDetail(detail === s.id ? null : s.id)
                                  }
                                >
                                  <Icon name="chevron" size={17} />
                                </button>
                              </div>
                            </td>
                          </tr>
                          {detail === s.id && (
                            <tr className="detail-row">
                              <td colSpan={6}>
                                <dl className="metadata-grid">
                                  <dt>Signal ID</dt>
                                  <dd>{s.id}</dd>
                                  <dt>Observed</dt>
                                  <dd>{observationTime(c.time)}</dd>
                                  <dt>Received</dt>
                                  <dd>{observationTime(c.time + 0.2)}</dd>
                                  <dt>Source</dt>
                                  <dd>{s.source}</dd>
                                  <dt>Cadence</dt>
                                  <dd>{s.period} s</dd>
                                  <dt>Origin</dt>
                                  <dd>Preview sample</dd>
                                  <dt>Component</dt>
                                  <dd>{s.component}</dd>
                                  <dt>Model</dt>
                                  <dd>Sample fixture v1</dd>
                                </dl>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      ))}
                    </Fragment>
                  )
                );
              })}
            </tbody>
          </table>
        </div>
        {!visible.length && (
          <Empty
            title="No matching measurements"
            text="Adjust the search or filters to show readings."
          />
        )}
      </Panel>
    </>
  );
}

export function Fleet({ c }: { c: PreviewContext }) {
  const [vehicles, setVehicles] = useState<Vehicle[]>(["bus-001", "bus-002"]),
    [metric, setMetric] = useState("battery.pack.soc"),
    [selected, setSelected] = useState<string | null>(null);
  const signal = signalById(metric),
    percent = metric === "battery.pack.soc";
  return (
    <>
      <div className="filter-bar fleet-filter">
        <Icon name="filter" />
        <span>Vehicles</span>
        {(["bus-001", "bus-002"] as Vehicle[]).map((v) => (
          <label className="check-pill" key={v}>
            <input
              type="checkbox"
              checked={vehicles.includes(v)}
              onChange={(e) =>
                setVehicles(
                  e.target.checked
                    ? [...vehicles, v]
                    : vehicles.filter((x) => x !== v),
                )
              }
            />
            {vehicleName(v)}
          </label>
        ))}
        <span className="muted push-right">{vehicles.length} selected</span>
      </div>
      {!vehicles.length ? (
        <Panel>
          <Empty
            title="Select a vehicle"
            text="Choose one or both vehicles to compare registered readings."
          />
        </Panel>
      ) : (
        <div className="fleet-grid">
          <Panel title="Measurement comparison">
            <div className="table-scroll">
              <table className="fleet-table">
                <thead>
                  <tr>
                    <th>Signal</th>
                    <th>Unit</th>
                    {vehicles.map((v) => (
                      <th key={v}>
                        {vehicleName(v)}
                        <small>
                          <Status
                            label={
                              v === "bus-002"
                                ? "Paused · Stale"
                                : isStale(c)
                                  ? "Stale"
                                  : "Running"
                            }
                            tone={
                              v === "bus-002" || isStale(c) ? "amber" : "teal"
                            }
                          />
                        </small>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {groups.map((g) => (
                    <Fragment key={g.name}>
                      <tr className={`group-row ${g.tone}`}>
                        <th colSpan={vehicles.length + 2}>{g.name}</th>
                      </tr>
                      {g.ids.map((id) => {
                        const s = signalById(id);
                        return (
                          <tr key={id}>
                            <td>{s.label}</td>
                            <td>{s.unit}</td>
                            {vehicles.map((v) => (
                              <td key={v}>
                                <button
                                  className="value-button"
                                  onClick={() =>
                                    setSelected(
                                      `${vehicleName(v)} · ${s.label} · ${s.component} · ${s.source} · ${v === "bus-002" ? "7.2 s old · Stale" : `${number(c.age, 1)} s old`} · Observed ${observationTime(v === "bus-002" ? 120 : c.time)}`,
                                    )
                                  }
                                >
                                  {number(
                                    v === "bus-001"
                                      ? currentValue(id, { ...c, vehicle: v })
                                      : sampleValue(id, 120, v),
                                    id === "battery.pack.soc" ? 4 : 2,
                                  )}
                                </button>
                              </td>
                            ))}
                          </tr>
                        );
                      })}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <aside className="stack">
            <Panel title="Compare measurements" className="tinted violet">
              <select
                aria-label="Fleet comparison metric"
                value={metric}
                onChange={(e) => setMetric(e.target.value)}
              >
                {signals.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.label}
                  </option>
                ))}
              </select>
              <Bars
                items={vehicles.map((v, i) => ({
                  label: vehicleName(v),
                  value:
                    v === "bus-001" &&
                    currentValue(metric, { ...c, vehicle: v }) == null
                      ? null
                      : sampleValue(metric, v === "bus-002" ? 120 : c.time, v) *
                        (percent ? 100 : 1),
                  color: i ? palette.violet : palette.pink,
                }))}
                unit={percent ? "%" : signal.unit}
                maximum={percent ? 100 : undefined}
              />
            </Panel>
            <Panel title="Selection" className="gradient charcoal">
              <div className="source-value">{vehicles.length} vehicles</div>
              <p>All registered measurements appear in the comparison.</p>
              <p className="fine">
                Select a value to inspect its source and age.
              </p>
            </Panel>
            {selected && (
              <Panel
                title="Measurement detail"
                action={
                  <button
                    className="icon-button"
                    aria-label="Close measurement detail"
                    onClick={() => setSelected(null)}
                  >
                    <Icon name="close" size={17} />
                  </button>
                }
              >
                <p className="detail-copy">{selected}</p>
              </Panel>
            )}
            <div className="notice amber">
              <Icon name="clock" />
              <span>
                Bus 002 holds its last observed readings while paused.
              </span>
            </div>
          </aside>
        </div>
      )}
    </>
  );
}
