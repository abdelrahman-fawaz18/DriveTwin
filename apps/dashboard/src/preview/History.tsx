import { useMemo, useState } from "react";
import { Chart } from "../ui/Chart.tsx";
import { Icon } from "../ui/Icon.tsx";
import {
  csvCell,
  download,
  number,
  observationTime,
  series,
  signalById,
  signals,
  statistics,
} from "./data.ts";
import { Details, Panel } from "./ui.tsx";
import type { PreviewContext } from "./ui.tsx";

export function History({ c }: { c: PreviewContext }) {
  const initial = Array.from(
    new Set([c.historySignal, "motion.speed", "traction.motor.temperature"]),
  );
  const [selection, setSelection] = useState(initial),
    [from, setFrom] = useState(60),
    [to, setTo] = useState(120),
    [error, setError] = useState("");
  const [query, setQuery] = useState({
    from: 60,
    to: 120,
    signals: initial,
    vehicle: c.vehicle,
    revision: c.time,
  });
  const [cursor, setCursor] = useState(100),
    [page, setPage] = useState(0),
    [bucket, setBucket] = useState(15),
    [gap, setGap] = useState(true);
  const plots = useMemo(
    () =>
      query.signals.map((id) => ({
        signal: signalById(id),
        points: series(id, query.from, query.to, query.vehicle, gap),
      })),
    [query, gap],
  );
  const rows = useMemo(
    () =>
      plots.flatMap((p) =>
        p.points.map((point) => ({
          signal_id: p.signal.id,
          unit: p.signal.unit,
          working_time_s: point.t,
          value: point.value,
          quality: point.value == null ? "Missing" : "Good",
          observed: observationTime(point.t),
          received: observationTime(point.t + 0.2),
          source: "Preview sample",
        })),
      ),
    [plots],
  );
  const apply = () => {
    if (
      !Number.isFinite(from) ||
      !Number.isFinite(to) ||
      from < 0 ||
      to <= from ||
      to > c.time
    ) {
      setError(
        `Choose a range between 0 and ${c.time} seconds, with its end after its start.`,
      );
      return;
    }
    if (
      !selection.length ||
      selection.length > 5 ||
      (Math.floor(to) - Math.ceil(from) + 1) * selection.length > 5000
    ) {
      setError(
        "Select one to five signals and a range containing at most 5,000 total readings.",
      );
      return;
    }
    setError("");
    setQuery({
      from,
      to,
      signals: [...selection],
      vehicle: c.vehicle,
      revision: c.time,
    });
    setCursor(from);
    setPage(0);
  };
  const exportData = (format: "CSV" | "JSON") => {
    const metadata = {
      origin: "Preview sample",
      vehicle: query.vehicle,
      from_s: query.from,
      to_s: query.to,
      revision: query.revision,
      signals: query.signals,
      units: "Stored",
      gaps_included: gap,
    };
    const keys = [
      "signal_id",
      "unit",
      "working_time_s",
      "value",
      "quality",
      "observed",
      "received",
      "source",
    ] as const;
    const text =
      format === "JSON"
        ? JSON.stringify({ metadata, readings: rows }, null, 2)
        : [
            "record_type," + keys.join(","),
            ["metadata", JSON.stringify(metadata), ...Array(7).fill("")]
              .map(csvCell)
              .join(","),
            ...rows.map((row) =>
              ["reading", ...keys.map((k) => row[k])].map(csvCell).join(","),
            ),
          ].join("\r\n");
    download(
      `drivetwin-preview-history.${format.toLowerCase()}`,
      text,
      format === "JSON" ? "application/json" : "text/csv",
    );
  };
  const buckets = plots.flatMap((plot) =>
    Array.from(
      { length: Math.ceil((query.to - query.from + 1) / bucket) },
      (_, i) => {
        const start = query.from + i * bucket,
          end = Math.min(query.to, start + bucket - 1);
        return {
          signal: plot.signal.label,
          unit: plot.signal.unit,
          start,
          end,
          ...statistics(plot.points.filter((p) => p.t >= start && p.t <= end)),
        };
      },
    ),
  );
  return (
    <>
      <div className="filter-bar history-toolbar">
        <label>
          From (s)
          <input
            type="number"
            min={0}
            value={from}
            onChange={(e) => setFrom(Number(e.target.value))}
          />
        </label>
        <label>
          To (s)
          <input
            type="number"
            min={1}
            value={to}
            onChange={(e) => setTo(Number(e.target.value))}
          />
        </label>
        <button className="primary" onClick={apply}>
          Apply
        </button>
        <button
          className="subtle"
          onClick={() => {
            setFrom(Math.max(0, c.time - 60));
            setTo(c.time);
          }}
        >
          Last 60 s
        </button>
        <div className="export-actions">
          <button onClick={() => exportData("CSV")}>
            <Icon name="download" size={16} />
            Export CSV
          </button>
          <button onClick={() => exportData("JSON")}>
            <Icon name="download" size={16} />
            Export JSON
          </button>
        </div>
      </div>
      {error && (
        <p className="notice amber" role="alert">
          {error}
        </p>
      )}
      <details className="panel signal-picker">
        <summary>
          Select signals <span>{selection.length} / 5</span>
          <Icon name="chevron" size={16} />
        </summary>
        <div className="signal-options">
          {signals.map((s) => (
            <label key={s.id}>
              <input
                type="checkbox"
                checked={selection.includes(s.id)}
                disabled={!selection.includes(s.id) && selection.length === 5}
                onChange={(e) =>
                  setSelection(
                    e.target.checked
                      ? [...selection, s.id]
                      : selection.filter((id) => id !== s.id),
                  )
                }
              />
              {s.label}
            </label>
          ))}
        </div>
      </details>
      <Panel className="history-charts">
        <div className="chart-heading">
          <span>
            Applied window {query.from}–{query.to} s
          </span>
          <span className="muted push-right">
            Snapshot {query.revision} · {query.signals.length} signals
          </span>
        </div>
        {plots.map((plot) => (
          <div className="history-plot" key={plot.signal.id}>
            <h3>
              <i
                className="legend-dot"
                style={{ background: plot.signal.color }}
              />
              {plot.signal.label}
              <span>({plot.signal.unit})</span>
              <strong className="push-right">
                {number(
                  plot.points.find((p) => p.t === Math.round(cursor))?.value ??
                    null,
                  plot.signal.unit === "1" ? 4 : 2,
                )}
              </strong>
            </h3>
            <Chart
              points={plot.points}
              color={plot.signal.color}
              unit={plot.signal.unit}
              height={165}
              label={`${plot.signal.label} history`}
              cursor={cursor}
              onCursor={setCursor}
            />
          </div>
        ))}
        <div className="cursor-control">
          <label htmlFor="history-cursor">Inspect {number(cursor, 0)} s</label>
          <input
            id="history-cursor"
            type="range"
            min={query.from}
            max={query.to}
            step="1"
            value={cursor}
            onChange={(e) => setCursor(Number(e.target.value))}
          />
          <label className="inline-check">
            <input
              type="checkbox"
              checked={gap}
              onChange={(e) => {
                setGap(e.target.checked);
                setPage(0);
              }}
            />
            Example missing samples
          </label>
        </div>
      </Panel>
      <Details title="Interval statistics">
        <label className="inline-controls">
          Bucket width (s)
          <input
            type="number"
            min="1"
            max="300"
            value={bucket}
            onChange={(e) =>
              setBucket(Math.max(1, Math.min(300, Number(e.target.value) || 1)))
            }
          />
        </label>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Signal</th>
                <th>Interval (s)</th>
                <th>Good / missing</th>
                <th>Minimum</th>
                <th>Mean</th>
                <th>Maximum</th>
                <th>Unit</th>
              </tr>
            </thead>
            <tbody>
              {buckets.map((b, i) => (
                <tr key={i}>
                  <td>{b.signal}</td>
                  <td>
                    {b.start}–{b.end}
                  </td>
                  <td>
                    {b.count} / {b.missing}
                  </td>
                  <td>{number(b.min, 4)}</td>
                  <td>{number(b.mean, 4)}</td>
                  <td>{number(b.max, 4)}</td>
                  <td>{b.unit}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Details>
      <Details title={`Recorded values · ${rows.length} readings`}>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Signal</th>
                <th>Working time</th>
                <th>Value</th>
                <th>Unit</th>
                <th>Quality</th>
                <th>Observed / received</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(page * 20, page * 20 + 20).map((r, i) => (
                <tr key={i}>
                  <td>{signalById(r.signal_id).label}</td>
                  <td>{r.working_time_s} s</td>
                  <td>{number(r.value, 4)}</td>
                  <td>{r.unit}</td>
                  <td>{r.quality}</td>
                  <td>
                    {r.observed}
                    <small>{r.received}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="pagination">
          <button disabled={page === 0} onClick={() => setPage(page - 1)}>
            Previous
          </button>
          <span>
            Page {page + 1} of {Math.max(1, Math.ceil(rows.length / 20))}
          </span>
          <button
            disabled={(page + 1) * 20 >= rows.length}
            onClick={() => setPage(page + 1)}
          >
            Next
          </button>
        </div>
      </Details>
    </>
  );
}
