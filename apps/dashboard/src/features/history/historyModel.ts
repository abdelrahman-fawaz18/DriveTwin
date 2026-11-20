import { parseHistory } from "../../monitoring/model.ts";
import type { Point, Snapshot } from "../../monitoring/model.ts";

export interface HistoryQuery {
  snap: Snapshot;
  signals: string[];
  bounds: { from: number; to: number };
}
export interface PlotPoint extends Point {
  signal_id: string;
  segment_id: string;
}
export interface Bucket {
  signal_id: string;
  from_s: number;
  to_s: number;
  count: number;
  good: number;
  missing: number;
  invalid: number;
  continuity_breaks: number;
  minimum: number | null;
  maximum: number | null;
  mean: number | null;
  first_good_s: number | null;
  last_good_s: number | null;
}
type RecordValue = Record<string, unknown>;
export interface Plot {
  metadata: RecordValue;
  items: PlotPoint[];
  buckets: Bucket[];
}
export interface ExportRow extends Point {
  signal_id: string;
  manifest_sha256: string;
  schema_version: string;
  catalog_version: string;
  run_id: string;
  asset_id: string;
  dashboard_revision: number;
}
export const EXPORT_ROW_LIMIT = 100_000;
export const EXPORT_BYTE_LIMIT = 64 * 1024 * 1024;
const finite = (n: unknown): n is number =>
  typeof n === "number" && Number.isFinite(n);
const integer = (n: unknown): n is number =>
  finite(n) && Number.isSafeInteger(n) && n >= 0;
const object = (v: unknown): v is RecordValue =>
  !!v && typeof v === "object" && !Array.isArray(v);
const fingerprint = (v: unknown) =>
  typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const uuid = (v: unknown) =>
  typeof v === "string" &&
  /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(v);
const version = (v: unknown) =>
  typeof v === "string" && /^\d+\.\d+\.\d+$/.test(v);

function fail(): never {
  throw Error(
    "History response does not match the frozen query or supported format",
  );
}
export function parameters(query: HistoryQuery): URLSearchParams {
  const p = new URLSearchParams({
    asset_id: query.snap.asset_id,
    as_of: query.snap.cursor,
    from_s: String(query.bounds.from),
    to_s: String(query.bounds.to),
  });
  query.signals.forEach((s) => p.append("signals", s));
  return p;
}
function metadata(body: unknown, query: HistoryQuery): RecordValue {
  if (!object(body) || !object(body.metadata)) return fail();
  const m = body.metadata;
  if (
    m.mode !== "simulation" ||
    m.run_id !== query.snap.run_id ||
    m.asset_id !== query.snap.asset_id ||
    m.as_of !== query.snap.cursor ||
    m.from_s !== query.bounds.from ||
    m.to_s !== query.bounds.to ||
    m.time_basis !== "simulation seconds" ||
    !fingerprint(m.policy_sha256) ||
    !version(m.catalog_version) ||
    !object(m.signals) ||
    !object(m.timings) ||
    query.signals.length < 1 ||
    query.signals.length > 5 ||
    new Set(query.signals).size !== query.signals.length ||
    Object.keys(m.signals).sort().join("\0") !==
      [...query.signals].sort().join("\0") ||
    Object.keys(m.timings).sort().join("\0") !==
      [...query.signals].sort().join("\0")
  )
    return fail();
  for (const signal of query.signals) {
    const expected = query.snap.items.find((i) => i.signal_id === signal);
    const definition = m.signals[signal];
    const timing = m.timings[signal];
    if (
      !expected ||
      !object(definition) ||
      !object(timing) ||
      definition.unit !== expected.definition.unit ||
      definition.component_id !== expected.definition.component_id ||
      definition.classification !== expected.definition.classification ||
      definition.sample_period_s !== expected.definition.sample_period_s ||
      timing.source_id !== expected.source_id ||
      !finite(timing.period_s) ||
      timing.period_s <= 0
    )
      return fail();
  }
  return m;
}
function point(p: unknown, query: HistoryQuery): Point & { signal_id: string } {
  if (
    !object(p) ||
    typeof p.signal_id !== "string" ||
    !query.signals.includes(p.signal_id)
  )
    return fail();
  parseHistory(
    {
      run_id: query.snap.run_id,
      asset_id: query.snap.asset_id,
      signal_id: p.signal_id,
      as_of: query.snap.cursor,
      resolution: "raw",
      items: [p],
      next: null,
    },
    query.snap,
    p.signal_id,
    query.snap.cursor,
  );
  const result = p as unknown as Point & { signal_id: string };
  const expected = query.snap.items.find(
    (i) => i.signal_id === result.signal_id,
  );
  if (
    !uuid(result.event_id) ||
    result.source_epoch < 1 ||
    result.stream_seq < 0 ||
    result.source_id !==
      query.snap.items.find((i) => i.signal_id === result.signal_id)
        ?.source_id ||
    result.sim_time_s < query.bounds.from ||
    result.sim_time_s > query.bounds.to ||
    !object(p.sample) ||
    p.sample.unit !== expected?.definition.unit ||
    p.sample.signal_id !== result.signal_id
  )
    return fail();
  return result;
}
function parseBuckets(
  rows: unknown,
  query: HistoryQuery,
  width: unknown,
): Bucket[] {
  if (
    !Array.isArray(rows) ||
    rows.length > 5000 ||
    !finite(width) ||
    width <= 0
  )
    return fail();
  const ratio = (query.bounds.to - query.bounds.from) / width;
  const count = Math.max(
    1,
    Math.ceil(
      Math.abs(ratio - Math.round(ratio)) < 1e-9 ? Math.round(ratio) : ratio,
    ),
  );
  if (
    count * query.signals.length > 5000 ||
    rows.length !== count * query.signals.length
  )
    return fail();
  const seen = new Set<string>();
  for (const r of rows) {
    if (
      !object(r) ||
      typeof r.signal_id !== "string" ||
      !query.signals.includes(r.signal_id) ||
      !finite(r.from_s) ||
      !finite(r.to_s) ||
      ![r.count, r.good, r.missing, r.invalid, r.continuity_breaks].every(
        integer,
      )
    )
      return fail();
    const index = Math.round((r.from_s - query.bounds.from) / width);
    const expectedFrom = query.bounds.from + index * width;
    const expectedTo = Math.min(
      query.bounds.to,
      query.bounds.from + (index + 1) * width,
    );
    const key = `${r.signal_id}:${index}`;
    if (
      index < 0 ||
      index >= count ||
      seen.has(key) ||
      Math.abs(r.from_s - expectedFrom) > 1e-7 ||
      Math.abs(r.to_s - expectedTo) > 1e-7 ||
      r.count !== Number(r.good) + Number(r.missing) + Number(r.invalid) ||
      Number(r.continuity_breaks) > Number(r.count)
    )
      return fail();
    seen.add(key);
    if (r.good === 0) {
      if (
        ![r.minimum, r.maximum, r.mean, r.first_good_s, r.last_good_s].every(
          (v) => v === null,
        )
      )
        return fail();
    } else if (
      ![r.minimum, r.maximum, r.mean, r.first_good_s, r.last_good_s].every(
        finite,
      ) ||
      Number(r.minimum) > Number(r.maximum) ||
      Number(r.minimum) - Number(r.mean) >
        1e-12 * Math.max(1, Math.abs(Number(r.minimum))) ||
      Number(r.mean) - Number(r.maximum) >
        1e-12 * Math.max(1, Math.abs(Number(r.maximum))) ||
      Number(r.first_good_s) < r.from_s ||
      Number(r.last_good_s) > r.to_s ||
      Number(r.first_good_s) > Number(r.last_good_s)
    )
      return fail();
  }
  return rows as Bucket[];
}
export function parsePlot(
  body: unknown,
  query: HistoryQuery,
  budget = 5000,
): Plot {
  const m = metadata(body, query);
  if (
    !object(body) ||
    !Array.isArray(body.items) ||
    m.plot_version !== "history-plot/1.0.0" ||
    m.resolution !==
      "first/minimum/maximum/last per bucket and continuous segment" ||
    m.interpolated !== false ||
    m.point_limit !== budget ||
    body.items.length > budget ||
    m.point_count !== body.items.length ||
    !object(m.quality_counts)
  )
    return fail();
  const buckets = parseBuckets(body.buckets, query, m.bucket_s);
  const totals = {
    count: 0,
    good: 0,
    missing: 0,
    invalid: 0,
    continuity_breaks: 0,
  };
  for (const bucket of buckets)
    for (const k of Object.keys(totals) as (keyof typeof totals)[])
      totals[k] += bucket[k];
  if (
    Object.entries(totals).some(
      ([k, v]) =>
        m.quality_counts && (m.quality_counts as RecordValue)[k] !== v,
    ) ||
    totals.good < body.items.length
  )
    return fail();
  const seen = new Set<string>(),
    lastBySignal = new Map<string, Point>();
  const items = body.items.map((raw): PlotPoint => {
    const p = point(raw, query);
    if (
      !object(raw) ||
      typeof raw.segment_id !== "string" ||
      !raw.segment_id.startsWith(`${p.source_id}:${p.source_epoch}:`) ||
      !/^[1-9]\d*$/.test(
        raw.segment_id.slice(`${p.source_id}:${p.source_epoch}:`.length),
      ) ||
      p.sample.value === null ||
      (p.sample.quality && p.sample.quality !== "good")
    )
      return fail();
    const key = `${p.signal_id}:${p.event_id}`;
    const last = lastBySignal.get(p.signal_id);
    if (
      seen.has(key) ||
      (last &&
        (p.sim_time_s < last.sim_time_s ||
          (p.sim_time_s === last.sim_time_s && p.event_id <= last.event_id)))
    )
      return fail();
    seen.add(key);
    lastBySignal.set(p.signal_id, p);
    return { ...p, segment_id: raw.segment_id };
  });
  // Verify each nonempty bucket's extrema survived, without synthesizing values.
  const byBucket = new Map<string, PlotPoint[]>();
  const count = buckets.length / query.signals.length;
  for (const p of items) {
    const i = Math.min(
      count - 1,
      Math.max(
        0,
        Math.floor((p.sim_time_s - query.bounds.from) / Number(m.bucket_s)),
      ),
    );
    const key = `${p.signal_id}:${i}`;
    const retained = byBucket.get(key) ?? [];
    retained.push(p);
    byBucket.set(key, retained);
  }
  for (const b of buckets) {
    const i = Math.round((b.from_s - query.bounds.from) / Number(m.bucket_s));
    const retained = byBucket.get(`${b.signal_id}:${i}`) ?? [];
    if (
      b.good &&
      (!retained.some((p) => p.sample.value === b.minimum) ||
        !retained.some((p) => p.sample.value === b.maximum))
    )
      return fail();
    if (
      retained.some(
        (p) => p.sample.value! < b.minimum! || p.sample.value! > b.maximum!,
      )
    )
      return fail();
  }
  return { metadata: m, items, buckets };
}
export function plotSegments(points: PlotPoint[]): Point[][] {
  const runs: Point[][] = [],
    closed = new Set<string>();
  let key = "";
  for (const p of points) {
    const next = `${p.signal_id}:${p.segment_id}`;
    if (next !== key) {
      if (closed.has(next)) return fail();
      if (key) closed.add(key);
      runs.push([]);
      key = next;
    }
    runs.at(-1)!.push(p);
  }
  return runs;
}
export function parseSummary(
  body: unknown,
  query: HistoryQuery,
  width: number,
): Bucket[] {
  const m = metadata(body, query);
  if (
    !object(body) ||
    m.summary_version !== "history-summary/1.0.0" ||
    m.resolution !== "database bucket summary" ||
    m.bucket_s !== width
  )
    return fail();
  return parseBuckets(body.items, query, width);
}
export function compareRows(
  a: Pick<ExportRow, "sim_time_s" | "event_id" | "signal_id">,
  b: Pick<ExportRow, "sim_time_s" | "event_id" | "signal_id">,
): number {
  return (
    a.sim_time_s - b.sim_time_s ||
    (a.event_id < b.event_id
      ? -1
      : a.event_id > b.event_id
        ? 1
        : a.signal_id < b.signal_id
          ? -1
          : a.signal_id > b.signal_id
            ? 1
            : 0)
  );
}
export function parseExportPage(
  body: unknown,
  query: HistoryQuery,
  limit: number,
  previous?: ExportRow,
) {
  const m = metadata(body, query);
  if (
    !object(body) ||
    m.export_version !== "1.1.0" ||
    m.resolution !== "raw" ||
    m.page_limit !== limit ||
    !Array.isArray(body.items) ||
    body.items.length > limit ||
    m.row_count !== body.items.length ||
    !(
      body.next === null ||
      (typeof body.next === "string" && body.next.length <= 1800)
    )
  )
    return fail();
  let last = previous;
  const revision = Number(query.snap.cursor.split(":")[2]);
  const items = body.items.map((raw): ExportRow => {
    const p = point(raw, query);
    if (
      !object(raw) ||
      raw.run_id !== query.snap.run_id ||
      raw.asset_id !== query.snap.asset_id ||
      !integer(raw.dashboard_revision) ||
      raw.dashboard_revision > revision ||
      !fingerprint(raw.manifest_sha256) ||
      !version(raw.schema_version) ||
      !version(raw.catalog_version) ||
      raw.catalog_version !== m.catalog_version ||
      (last && compareRows(last, p) >= 0)
    )
      return fail();
    const row = raw as unknown as ExportRow;
    last = row;
    return row;
  });
  if (body.next !== null) {
    if (!items.length || !last) return fail();
    const cursor: unknown = JSON.parse(body.next);
    const expected = [
      query.snap.cursor,
      "raw-export/1.0.0",
      [...query.signals].sort(),
      query.bounds.from,
      query.bounds.to,
      limit,
      last.sim_time_s,
      last.event_id,
      last.signal_id,
    ];
    if (JSON.stringify(cursor) !== JSON.stringify(expected)) return fail();
  }
  return { metadata: m, items, next: body.next as string | null };
}
export function exportCsv(metadata: RecordValue, rows: ExportRow[]): string {
  const fields = [
    "record_type",
    "metadata_json",
    "event_id",
    "signal_id",
    "unit",
    "sim_time_s",
    "observed_at",
    "received_at",
    "source_id",
    "source_epoch",
    "stream_seq",
    "quality",
    "value",
    "raw_value",
    "reason",
    "provenance",
    "manifest_sha256",
    "schema_version",
    "catalog_version",
  ];
  function cell(v: unknown): string {
    if (v === undefined || v === null) return "";
    let s = String(v);
    if (typeof v === "string" && /^[=+\-@\t\r]/.test(s.trimStart()))
      s = "'" + s;
    return /[,"\r\n]/.test(s) ? '"' + s.replaceAll('"', '""') + '"' : s;
  }
  const lines = [
    fields.join(","),
    ["metadata", JSON.stringify(metadata), ...Array(fields.length - 2).fill("")]
      .map(cell)
      .join(","),
  ];
  for (const r of rows) {
    const definitions = metadata.signals as Record<string, { unit: string }>;
    const record: RecordValue = {
      ...r,
      ...r.sample,
      record_type: "sample",
      unit: definitions[r.signal_id]?.unit,
    };
    lines.push(fields.map((f) => cell(record[f])).join(","));
  }
  return lines.join("\r\n") + "\r\n";
}
