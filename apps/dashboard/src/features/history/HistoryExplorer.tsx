import { useEffect, useRef, useState } from "react";
import { Loading } from "../../ui/Loading.tsx";
import { SectionHeading } from "../../ui/SectionHeading.tsx";
import type { Snapshot } from "../../monitoring/model.ts";
import { label, value } from "../../monitoring/model.ts";
import { Trend } from "../../ui/Trend.tsx";
import {
  EXPORT_BYTE_LIMIT,
  EXPORT_ROW_LIMIT,
  exportCsv,
  compareRows,
  parameters,
  parseExportPage,
  parsePlot,
  parseSummary,
  plotSegments,
} from "./historyModel.ts";
import {
  historyBatches,
  historyJson,
  sameHistorySnapshot,
  validateSummaryBudget,
} from "./historyRequests.ts";
import type { HistoryEntry } from "../forecast/forecastNavigation.ts";
import { SignalPicker } from "./SignalPicker.tsx";
import { tripEventLabel, type TripEvent } from "../trip/tripReview.ts";
import type { Bucket, ExportRow, Plot } from "./historyModel.ts";

export function HistoryExplorer({
  snap,
  token,
  initialSignal,
  entry,
  backToForecast,
  tripEvents = [],
}: {
  snap: Snapshot;
  token: string;
  initialSignal?: string;
  entry?: HistoryEntry;
  backToForecast?: () => void;
  tripEvents?: TripEvent[];
}) {
  const newest = Math.max(
    0,
    ...snap.items.map((i) => i.observation?.sim_time_s ?? 0),
  );
  const initialSignals = (
    entry?.signals ?? (initialSignal ? [initialSignal] : [])
  ).filter((id) => snap.items.some((i) => i.signal_id === id));
  const picker = useRef<HTMLDetailsElement>(null);
  const [signals, setSignals] = useState(initialSignals),
    [from, setFrom] = useState(entry?.from ?? 0),
    [to, setTo] = useState(entry?.to ?? Math.max(1, newest));
  const [query, setQuery] = useState({
      snap,
      signals: initialSignals,
      bounds: { from, to },
    }),
    [cursor, setCursor] = useState(entry?.eventTime ?? entry?.cutoff ?? from),
    [error, setError] = useState(""),
    [working, setWorking] = useState(false);
  const [bucket, setBucket] = useState(5),
    [summary, setSummary] = useState<Bucket[] | null>(null);
  const generation = useRef(0);
  const operation = useRef<AbortController | null>(null);
  const [plot, setPlot] = useState<{
    query: typeof query;
    data: Plot | null;
    loading: boolean;
    error: string;
  }>({ query, data: null, loading: true, error: "" });
  const [progress, setProgress] = useState("");
  useEffect(() => () => operation.current?.abort(), []);
  useEffect(() => {
    const own = new AbortController();
    setPlot({ query, data: null, loading: true, error: "" });
    void (async () => {
      try {
        const data: Plot = { metadata: {}, items: [], buckets: [] };
        for (const batch of historyBatches(query)) {
          const p = parameters(batch);
          p.set("point_limit", "5000");
          const { body } = await historyJson(
            "/v1/plot?" + p,
            token,
            own.signal,
          );
          const part = parsePlot(body, batch);
          sameHistorySnapshot(
            data.metadata.policy_sha256 ? data.metadata : undefined,
            part.metadata,
          );
          data.metadata = part.metadata;
          for (const signal of batch.signals)
            plotSegments(part.items.filter((p) => p.signal_id === signal));
          data.items.push(...part.items);
          data.buckets.push(...part.buckets);
          if (own.signal.aborted) return;
          setPlot({
            query,
            data: {
              ...data,
              items: [...data.items],
              buckets: [...data.buckets],
            },
            loading: true,
            error: "",
          });
        }
        if (!own.signal.aborted)
          setPlot({ query, data, loading: false, error: "" });
      } catch {
        if (!own.signal.aborted)
          setPlot({
            query,
            data: null,
            loading: false,
            error:
              "History unavailable or a batch exceeded its point/time budget. Shorten the interval or retry the query.",
          });
      }
    })();
    return () => own.abort();
  }, [query, token]);
  function selectSignals(next: string[]) {
    setSignals(next);
    generation.current++;
    operation.current?.abort();
    operation.current = null;
    setWorking(false);
    setSummary(null);
    setProgress("");
    setError("");
    // Keep the same committed snapshot and interval across checkbox changes.
    setQuery((old) => ({ ...old, signals: next }));
  }
  function apply() {
    if (
      !Number.isFinite(from) ||
      !Number.isFinite(to) ||
      from < 0 ||
      to < from ||
      to - from > 86400
    ) {
      setError("Choose an ordered range of at most 86,400 working seconds.");
      return;
    }
    setError("");
    generation.current++;
    operation.current?.abort();
    operation.current = null;
    setWorking(false);
    setProgress("");
    setSummary(null);
    setCursor(from);
    setQuery({ snap, signals: [...signals], bounds: { from, to } });
  }
  async function download(format: "json" | "csv") {
    const own = new AbortController(),
      ownGeneration = generation.current;
    operation.current?.abort();
    operation.current = own;
    setWorking(true);
    setError("");
    setProgress("Preparing raw export…");
    const timeout = setTimeout(() => own.abort(), 120000);
    try {
      const rows: ExportRow[] = [];
      let bytes = 0,
        pages = 0;
      const batchMetadata: Record<string, unknown>[] = [];
      for (const batch of historyBatches(query)) {
        const p = parameters(batch);
        p.set("limit", "1000");
        let next: string | null = null,
          binding = "",
          previous: ExportRow | undefined;
        let metadata: Record<string, unknown> = {};
        do {
          if (next) p.set("after", next);
          const page = await historyJson(
            "/v1/export-page?" + p,
            token,
            own.signal,
            Math.min(8 * 1024 * 1024, EXPORT_BYTE_LIMIT - bytes),
          );
          bytes += page.bytes;
          const parsed = parseExportPage(page.body, batch, 1000, previous);
          const { row_count: _count, ...stable } = parsed.metadata;
          const currentBinding = JSON.stringify(stable);
          if (binding && binding !== currentBinding)
            throw Error("Export metadata changed between pages");
          binding = currentBinding;
          metadata = stable;
          previous = parsed.items.at(-1) ?? previous;
          rows.push(...parsed.items);
          next = parsed.next;
          pages++;
          if (
            rows.length > EXPORT_ROW_LIMIT ||
            (rows.length === EXPORT_ROW_LIMIT && next)
          )
            throw Error("Export row budget exceeded");
          if (own.signal.aborted || ownGeneration !== generation.current)
            return;
          setProgress(
            `Preparing raw export: ${rows.length.toLocaleString("en-US")} readings…`,
          );
        } while (next);
        sameHistorySnapshot(batchMetadata[0], metadata);
        batchMetadata.push(metadata);
      }
      rows.sort(compareRows);
      const metadata = {
        ...batchMetadata[0],
        signals: Object.assign({}, ...batchMetadata.map((m) => m.signals)),
        timings: Object.assign({}, ...batchMetadata.map((m) => m.timings)),
        batch_count: batchMetadata.length,
        row_count: rows.length,
        page_count: pages,
        download_row_limit: EXPORT_ROW_LIMIT,
        download_complete: true,
      };
      const text =
        format === "json"
          ? JSON.stringify({ metadata, items: rows })
          : exportCsv(metadata, rows);
      const blob = new Blob([text], {
        type: format === "json" ? "application/json" : "text/csv;charset=utf-8",
      });
      if (blob.size > EXPORT_BYTE_LIMIT)
        throw Error("Export exceeds the browser download size limit");
      if (own.signal.aborted || ownGeneration !== generation.current) return;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `evdt-${query.snap.asset_id}.${format}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setProgress(
        `Downloaded ${rows.length.toLocaleString("en-US")} original readings from ${pages} page(s).`,
      );
    } catch {
      if (ownGeneration !== generation.current) return;
      setProgress("");
      setError(
        "Raw export unavailable, changed, timed out, or exceeds 100,000 readings / 64 MiB. No partial file was downloaded. Shorten the range or choose fewer signals.",
      );
    } finally {
      clearTimeout(timeout);
      if (operation.current === own) {
        operation.current = null;
        setWorking(false);
      }
    }
  }
  async function aggregate() {
    const ownGeneration = generation.current;
    const own = new AbortController();
    operation.current?.abort();
    operation.current = own;
    setWorking(true);
    setError("");
    const timeout = setTimeout(() => own.abort(), 120000);
    try {
      const rows: Bucket[] = [];
      validateSummaryBudget(query, bucket);
      let firstMeta: Record<string, unknown> | undefined;
      for (const batch of historyBatches(query)) {
        const p = parameters(batch);
        p.set("bucket_s", String(bucket));
        const { body } = await historyJson(
          "/v1/history-summary?" + p,
          token,
          own.signal,
        );
        rows.push(...parseSummary(body, batch, bucket));
        const meta = (body as { metadata: Record<string, unknown> }).metadata;
        sameHistorySnapshot(firstMeta, meta);
        firstMeta ??= meta;
      }
      if (ownGeneration === generation.current && !own.signal.aborted)
        setSummary(rows);
    } catch {
      if (ownGeneration !== generation.current) return;
      setSummary(null);
      setError(
        "Summary unavailable. Use positive bucket widths and at most 5,000 total signal intervals.",
      );
    } finally {
      clearTimeout(timeout);
      if (operation.current === own) {
        operation.current = null;
        setWorking(false);
      }
    }
  }
  return (
    <div className="history-explorer">
      {entry?.forecast && (
        <section className="panel history-origin">
          <div>
            <h2>Opened from Energy Forecast</h2>
            <p>
              Forecast cutoff {value(entry.cutoff)} working s ·{" "}
              {entry.comparison
                ? "Completed comparison"
                : "Recorded observations"}
            </p>
          </div>
          <button onClick={backToForecast}>Back to forecast</button>
        </section>
      )}
      <section className="panel history-query">
        <div className="panel-head">
          <SectionHeading
            eyebrow="RECORDED READINGS"
            title="Choose a recorded interval"
            icon="History"
            tone="indigo"
          />
          <span className="muted">Working seconds · original observations</span>
        </div>
        <div className="query-range">
          <label>
            From (working seconds)
            <input
              aria-label="From working seconds"
              type="number"
              min="0"
              step="0.2"
              value={from}
              onChange={(e) => setFrom(Number(e.target.value))}
            />
          </label>
          <label>
            To (working seconds)
            <input
              aria-label="To working seconds"
              type="number"
              min="0"
              step="0.2"
              value={to}
              onChange={(e) => setTo(Number(e.target.value))}
            />
          </label>
          <button className="secondary" onClick={() => setTo(newest)}>
            Use latest time
          </button>
          <button className="primary" onClick={apply}>
            Apply historical query
          </button>
        </div>
        <SignalPicker
          items={snap.items.map((i) => ({
            id: i.signal_id,
            unit: i.definition.unit,
          }))}
          selected={signals}
          onSelect={selectSignals}
          disclosure={picker}
        />
        <div
          className="history-presets"
          role="group"
          aria-label="Signal shortcuts"
        >
          <span className="fine">Quick selections</span>
          {[
            ["Battery", "battery."],
            ["Motion", "motion."],
          ].map(([name, prefix]) => {
            const available = snap.items
              .filter(({ signal_id }) => signal_id.startsWith(prefix!))
              .map(({ signal_id }) => signal_id);
            const selected =
              available.length > 0 &&
              available.every((id) => signals.includes(id));
            return (
              <button
                type="button"
                key={name}
                disabled={!available.length}
                aria-pressed={selected}
                onClick={() =>
                  selectSignals(
                    selected
                      ? signals.filter((id) => !available.includes(id))
                      : [...new Set([...signals, ...available])],
                  )
                }
              >
                {name}
              </button>
            );
          })}
        </div>
        <div className="history-selected">
          {signals.map((id) => (
            <button
              key={id}
              onClick={() => selectSignals(signals.filter((s) => s !== id))}
              aria-label={`Remove ${label(id)}`}
            >
              {label(id)} ×
            </button>
          ))}
        </div>
        <div className="query-actions">
          <button
            className="secondary"
            disabled={working || !query.signals.length}
            onClick={() => void download("json")}
          >
            Download JSON
          </button>
          <button
            className="secondary"
            disabled={working || !query.signals.length}
            onClick={() => void download("csv")}
          >
            Download CSV
          </button>
          <span className="muted">
            Exports include query, units, quality, recipe, model, and version
            metadata.
          </span>
        </div>
        {(progress || working) && (
          <p className="query-note" role="status">
            {working ? (
              <Loading label={progress || "Preparing recorded readings…"} />
            ) : (
              progress
            )}
          </p>
        )}
        <p className="query-note">
          Selected interval: {value(query.bounds.from)}–{value(query.bounds.to)}{" "}
          s · {query.signals.length} signal(s). Apply again to include newer
          committed readings.
        </p>
        {error && (
          <p className="error query-note" role="alert">
            {error}
          </p>
        )}
      </section>
      {!query.signals.length && (
        <section className="panel history-empty">
          <h2>Choose signals to explore</h2>
          <p>
            Select one or more signals. Each chart shares the same working-time
            cursor.
          </p>
          <button
            onClick={() => {
              if (picker.current) {
                picker.current.open = true;
                picker.current.querySelector("input")?.focus();
              }
            }}
          >
            Select signals
          </button>
        </section>
      )}
      {query.signals.length > 0 && (
        <section className="panel cursor-controls">
          <label htmlFor="history-cursor">
            Shared time cursor · {value(cursor)} s
          </label>
          <input
            id="history-cursor"
            aria-label="Shared time cursor"
            type="range"
            min={query.bounds.from}
            max={Math.max(query.bounds.from + 0.2, query.bounds.to)}
            step="0.2"
            value={cursor}
            onChange={(e) => setCursor(Number(e.target.value))}
          />
          <p>
            Move the slider with the arrow keys, or point at a chart. Each
            signal has its own labeled vertical axis. Recorded values and
            downloads retain stored units.
          </p>
          {tripEvents.some(
            (event) =>
              event.working >= query.bounds.from &&
              event.working <= query.bounds.to,
          ) && (
            <div
              className="history-presets"
              aria-label="Trip events in this interval"
            >
              <span className="fine">Jump to event</span>
              {tripEvents
                .filter(
                  (event) =>
                    event.working >= query.bounds.from &&
                    event.working <= query.bounds.to,
                )
                .map((event) => (
                  <button
                    key={event.id}
                    type="button"
                    onClick={() => setCursor(event.working)}
                  >
                    {value(event.working)} s · {tripEventLabel(event)}
                  </button>
                ))}
            </div>
          )}
        </section>
      )}
      <div className="history-plots">
        {query.signals.map((signal) => {
          const selected = query.snap.items.find((i) => i.signal_id === signal);
          return selected ? (
            <section
              className="panel trend-panel"
              key={signal}
              data-history-signal={signal}
            >
              <div className="panel-head">
                <div>
                  <p className="eyebrow">
                    RECORDED OBSERVATIONS · {selected.definition.classification}
                  </p>
                  <h2>{label(signal)}</h2>
                </div>
                <div>
                  <span>
                    {signal === "battery.pack.soc" &&
                    selected.definition.unit === "1"
                      ? "%"
                      : signal === "battery.pack.energy" &&
                          selected.definition.unit === "J"
                        ? "kWh"
                        : selected.definition.unit}
                  </span>
                  <button
                    className="history-remove"
                    aria-label={`Remove chart ${label(signal)}`}
                    onClick={() =>
                      selectSignals(signals.filter((s) => s !== signal))
                    }
                  >
                    ×
                  </button>
                </div>
              </div>
              <Trend
                snap={query.snap}
                selected={selected}
                token={token}
                windowS={1800}
                bounds={query.bounds}
                pinned
                markerS={entry?.cutoff}
                tripEvents={tripEvents}
                display={
                  signal === "battery.pack.soc" &&
                  selected.definition.unit === "1"
                    ? { factor: 100, unit: "%", color: "#087f76" }
                    : signal === "battery.pack.energy" &&
                        selected.definition.unit === "J"
                      ? { factor: 1 / 3600000, unit: "kWh", color: "#2062b6" }
                      : undefined
                }
                cursorS={cursor}
                onCursor={setCursor}
                provided={{
                  items:
                    plot.query === query
                      ? (plot.data?.items.filter(
                          (p) => p.signal_id === signal,
                        ) ?? [])
                      : [],
                  runs:
                    plot.query === query
                      ? plotSegments(
                          plot.data?.items.filter(
                            (p) => p.signal_id === signal,
                          ) ?? [],
                        )
                      : [],
                  loading: plot.query !== query || plot.loading,
                  error: plot.query === query ? plot.error : "",
                  rawCount:
                    plot.query === query
                      ? (plot.data?.buckets
                          .filter((b) => b.signal_id === signal)
                          .reduce((n, b) => n + b.count, 0) ?? 0)
                      : 0,
                  asOf: query.snap.as_of,
                }}
              />
            </section>
          ) : null;
        })}
      </div>
      <section className="panel summaries">
        <div className="panel-head">
          <div>
            <p className="eyebrow">EXPLICIT AGGREGATION</p>
            <h2>Interval statistics</h2>
          </div>
          <div className="query-range">
            <label>
              Bucket width (seconds)
              <input
                aria-label="Bucket width seconds"
                type="number"
                min="0.2"
                step="0.2"
                value={bucket}
                onChange={(e) => setBucket(Number(e.target.value))}
              />
            </label>
            <button
              className="secondary"
              disabled={working || !query.signals.length}
              onClick={() => void aggregate()}
            >
              Calculate summary
            </button>
          </div>
        </div>
        <p className="query-note">
          Statistics use good numeric readings only. Missing or invalid readings
          count separately; empty buckets retain no numeric value.
        </p>
        {summary && (
          <div className="table-scroll">
            <table>
              <caption>
                Bucket summaries in stored units. Raw observations remain
                unchanged.
              </caption>
              <thead>
                <tr>
                  <th>Signal</th>
                  <th>Interval (s)</th>
                  <th>Good / missing / invalid</th>
                  <th>Min</th>
                  <th>Mean</th>
                  <th>Max</th>
                </tr>
              </thead>
              <tbody>
                {summary.slice(0, 500).map((b, n) => (
                  <tr key={n}>
                    <td>{label(b.signal_id)}</td>
                    <td>
                      {value(b.from_s)}–{value(b.to_s)}
                    </td>
                    <td>
                      {b.good} / {b.missing} / {b.invalid}
                    </td>
                    <td>{value(b.minimum)}</td>
                    <td>{value(b.mean)}</td>
                    <td>{value(b.maximum)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {summary.length > 500 && (
              <p className="query-note">
                Showing the first 500 of {summary.length} buckets. Increase
                bucket width to inspect a smaller result.
              </p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
