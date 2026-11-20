export type Quality = "good" | "missing" | "invalid" | "unknown";
export type Freshness = "fresh" | "paused" | "stale" | "unknown";
export interface Sample {
  value: number | null;
  quality?: Quality;
  reason?: string;
  raw_value?: number;
  provenance: string;
}
export interface Reading {
  signal_id: string;
  source_id: string;
  quality: Quality;
  freshness: Freshness;
  source_state: string;
  age_s: number | null;
  definition: {
    unit: string;
    component_id: string;
    physical_location: string;
    classification: string;
    provenance: string;
    model_version?: string;
    evidence_class?: string;
    sample_period_s: number;
    freshness_s: number;
  };
  observation: null | {
    event_id: string;
    sim_time_s: number;
    observed_at: string;
    received_at: string;
    sample: Sample;
  };
}
export interface Snapshot {
  version: string;
  run_id: string;
  asset_id: string;
  mode: "simulation";
  as_of: string;
  cursor: string;
  items: Reading[];
  resynchronized?: boolean;
  monitoring_closed?: true;
  trip_progress?: {
    remaining_m: number | null;
    next_stop_id: string | null;
    at_passenger_stop: boolean;
  };
}
export interface Point {
  event_id: string;
  sim_time_s: number;
  observed_at: string;
  received_at: string;
  source_id: string;
  source_epoch: number;
  stream_seq: number;
  sample: Sample;
}
export interface History {
  run_id: string;
  asset_id: string;
  signal_id: string;
  as_of: string;
  items: Point[];
  next: string | null;
  resolution: "raw";
}

const finite = (n: unknown): n is number =>
  typeof n === "number" && Number.isFinite(n);
const object = (x: unknown): x is Record<string, unknown> =>
  !!x && typeof x === "object" && !Array.isArray(x);
const date = (x: unknown) =>
  typeof x === "string" && Number.isFinite(Date.parse(x));
function validSample(x: unknown): boolean {
  return (
    object(x) &&
    (x.value === null || finite(x.value)) &&
    typeof x.provenance === "string" &&
    (x.reason === undefined || typeof x.reason === "string") &&
    (x.raw_value === undefined || finite(x.raw_value)) &&
    (x.quality === undefined ||
      ["good", "invalid", "missing"].includes(String(x.quality))) &&
    (x.quality !== "good" || finite(x.value)) &&
    (!["invalid", "missing"].includes(String(x.quality)) || x.value === null)
  );
}
export function parseSnapshot(
  x: unknown,
  asset: string,
  run?: string,
): Snapshot {
  if (
    !object(x) ||
    x.version !== "1.0.0" ||
    x.mode !== "simulation" ||
    x.asset_id !== asset ||
    typeof x.run_id !== "string" ||
    (run && x.run_id !== run) ||
    !date(x.as_of) ||
    typeof x.cursor !== "string" ||
    (x.monitoring_closed !== undefined && x.monitoring_closed !== true) ||
    (x.trip_progress !== undefined && !validTripProgress(x.trip_progress)) ||
    !Array.isArray(x.items) ||
    x.items.length > 5000
  )
    throw Error("Snapshot identity or format changed");
  const parts = x.cursor.split(":");
  if (
    parts.length !== 3 ||
    parts[0] !== x.run_id ||
    parts[1] !== asset ||
    !/^\d+$/.test(parts[2] ?? "") ||
    !Number.isSafeInteger(Number(parts[2]))
  )
    throw Error("Invalid snapshot cursor");
  const names = new Set<string>();
  for (const i of x.items) {
    const definition = object(i) && object(i.definition) ? i.definition : {};
    if (
      !object(i) ||
      typeof i.signal_id !== "string" ||
      names.has(i.signal_id) ||
      typeof i.source_id !== "string" ||
      !["good", "missing", "invalid", "unknown"].includes(String(i.quality)) ||
      !["fresh", "paused", "stale", "unknown"].includes(String(i.freshness)) ||
      !["running", "paused", "finished", "disconnected", "unknown"].includes(
        String(i.source_state),
      ) ||
      !(i.age_s === null || finite(i.age_s)) ||
      !object(i.definition) ||
      ![
        "unit",
        "component_id",
        "physical_location",
        "classification",
        "provenance",
      ].every((k) => typeof definition[k] === "string") ||
      !finite(i.definition.sample_period_s) ||
      i.definition.sample_period_s <= 0 ||
      !finite(i.definition.freshness_s) ||
      i.definition.freshness_s < i.definition.sample_period_s ||
      !(
        i.observation === null ||
        (object(i.observation) &&
          finite(i.observation.sim_time_s) &&
          typeof i.observation.event_id === "string" &&
          date(i.observation.observed_at) &&
          date(i.observation.received_at) &&
          validSample(i.observation.sample))
      )
    )
      throw Error("Invalid measurement in snapshot");
    names.add(i.signal_id);
  }
  return x as unknown as Snapshot;
}

function validTripProgress(x: unknown) {
  return (
    object(x) &&
    (x.remaining_m === null ||
      (finite(x.remaining_m) &&
        x.remaining_m >= 0 &&
        x.remaining_m <= 10_000_000)) &&
    (x.next_stop_id === null ||
      (typeof x.next_stop_id === "string" &&
        /^[A-Za-z0-9_.:-]{1,96}$/.test(x.next_stop_id))) &&
    typeof x.at_passenger_stop === "boolean" &&
    (!x.at_passenger_stop || x.next_stop_id !== null)
  );
}
export function parseHistory(
  x: unknown,
  snap: Snapshot,
  signal: string,
  anchor: string,
): History {
  if (
    !object(x) ||
    x.run_id !== snap.run_id ||
    x.asset_id !== snap.asset_id ||
    x.signal_id !== signal ||
    x.as_of !== anchor ||
    x.resolution !== "raw" ||
    !Array.isArray(x.items) ||
    x.items.length > 1000 ||
    !(x.next === null || typeof x.next === "string")
  )
    throw Error("History identity or format changed");
  for (const p of x.items)
    if (
      !object(p) ||
      !finite(p.sim_time_s) ||
      !validSample(p.sample) ||
      typeof p.event_id !== "string" ||
      !date(p.observed_at) ||
      !date(p.received_at) ||
      typeof p.source_id !== "string" ||
      !Number.isSafeInteger(p.source_epoch) ||
      !Number.isSafeInteger(p.stream_seq)
    )
      throw Error("Invalid history point");
  return x as unknown as History;
}
export const label = (name: string) =>
  name
    .split(".")
    .map((s) => s[0]?.toUpperCase() + s.slice(1))
    .join(" · ");
const numberFormat = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 3,
});
const timeFormat = new Intl.DateTimeFormat("en-US", {
  hour12: false,
  timeZone: "UTC",
  hour: "numeric",
  minute: "numeric",
  second: "numeric",
});
export const value = (n: number | null | undefined) =>
  n == null ? "—" : numberFormat.format(n);
export const time = (iso?: string) => {
  if (!iso) return "Not observed";
  const date = new Date(iso);
  return (
    (Number.isNaN(date.getTime()) ? "Invalid Date" : timeFormat.format(date)) +
    " UTC"
  );
};

// Split first, then reduce each good segment. Invalid/missing/cadence gaps never join.
// Each bucket retains both extrema in time order, unlike every-nth-point sampling.
export function segments(
  points: Point[],
  gap: number,
  budget = 180,
): Point[][] {
  const runs: Point[][] = [];
  let current: Point[] = [];
  for (const p of points) {
    const prev = current.at(-1);
    if (
      p.sample.value === null ||
      (p.sample.quality && p.sample.quality !== "good")
    ) {
      if (current.length) runs.push(current);
      current = [];
      continue;
    }
    if (
      prev &&
      (p.sim_time_s - prev.sim_time_s > gap ||
        p.sim_time_s <= prev.sim_time_s ||
        p.source_epoch !== prev.source_epoch ||
        p.source_id !== prev.source_id)
    ) {
      runs.push(current);
      current = [];
    }
    current.push(p);
  }
  if (current.length) runs.push(current);
  return runs.map((run) => {
    if (run.length <= budget) return run;
    const step = Math.ceil(
      run.length / Math.max(1, Math.floor((budget - 2) / 2)),
    );
    const chosen = new Set([0, run.length - 1]);
    for (let i = 0; i < run.length; i += step) {
      let min = i,
        max = i;
      for (let j = i; j < Math.min(run.length, i + step); j++) {
        if (run[j]!.sample.value! < run[min]!.sample.value!) min = j;
        if (run[j]!.sample.value! > run[max]!.sample.value!) max = j;
      }
      chosen.add(min);
      chosen.add(max);
    }
    return [...chosen].sort((a, b) => a - b).map((i) => run[i]!);
  });
}

// A bounded UTF-8 SSE parser, including messages split between network chunks.
export class Frames {
  private pending = "";
  push(chunk: string): string[] {
    this.pending += chunk;
    if (this.pending.length > 2_000_000) throw Error("Oversized update frame");
    this.pending = this.pending.replace(/\r\n/g, "\n");
    const parts = this.pending.split("\n\n");
    this.pending = parts.pop() ?? "";
    return parts
      .map((p) =>
        p
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trimStart())
          .join("\n"),
      )
      .filter(Boolean);
  }
}
