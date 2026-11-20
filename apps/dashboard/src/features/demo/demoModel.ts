import { parseContext, type MapContext } from "../map/mapModel.ts";
import type { RouteSummary } from "../forecast/routeEnergyModel.ts";

export type Choice = "charge" | "continue";
export type Frame = [number, (number | null)[], string[]?];
export type Signal = {
  id: string;
  unit: string;
  freshness: number;
  provenance: string;
};
export type Forecast = {
  id: string;
  available: number;
  cutoff: number;
  model: string;
  inputId: string;
  routeId: string;
  trigger: string;
  status: "available" | "infeasible" | "unavailable";
  reason: string | null;
  summary: null | {
    arrival: number | null;
    minimum: number;
    duration: number;
    distance: number;
    energyKwh: number;
    drawKwh: number;
    recoveryKwh: number;
    auxKwh: number;
    capacityKwh: number;
    assumptions: string[];
    ledger?: RouteSummary["ledger"];
  };
};
export type DemoEvent = { t: number; kind: string; title: string };
export type Summary = {
  duration: number;
  arrivalSoc: number;
  minimumSoc: number;
  deliveredKwh: number;
  reserveCrossing: number | null;
  completed: boolean;
};
export type Timeline = {
  frames: Frame[];
  forecasts: Forecast[];
  progress: { t: number; remainingM: number | null; atStop: boolean }[];
  transfers: { t: number; state: string; deliveredKwh: number }[];
  events: DemoEvent[];
  comparisons?: {
    available: number;
    from: number;
    until: number;
    predicted: number;
    observed: number;
    error: number;
  }[];
  summary?: Summary;
};
export type Descriptor = { path: string; bytes: number; sha256: string };
export type Manifest = {
  version: "branched-trip-demo/1.0.0" | "branched-trip-demo/1.1.0";
  id: string;
  title: string;
  region: string;
  basis: string;
  initialSoc: number;
  reserveSoc: number;
  decision: {
    at: number;
    stationId: string;
    stationName: string;
    targetSoc: number;
    chargingSeconds: number;
    minimumSocWithStop: number;
    arrivalSocWithStop: number;
    arrivalWorkingS: number;
    waitSeconds: number;
    energyKwh: number;
    choices: ["charge", "continue"];
  };
  files: Record<"catalog" | "map" | "common" | Choice, Descriptor>;
  attribution: string[];
  evidence: { kind: string; prefixSha256: string; sourceRevision: string };
};
export type DemoCase = {
  manifest: Manifest;
  catalog: Signal[];
  map: MapContext;
  common: Timeline;
  charge: Timeline;
  continue: Timeline;
};
const MAX_BYTES = 16 * 1024 * 1024;
const finite = (x: unknown): x is number =>
  typeof x === "number" && Number.isFinite(x);
const text = (x: unknown): x is string =>
  typeof x === "string" && x.length > 0 && x.length <= 1000;
const bounded = (x: unknown, lo = 0, hi = 14400): x is number =>
  finite(x) && x >= lo && x <= hi;
function require(condition: unknown, message: string): asserts condition {
  if (!condition) throw Error(message);
}
function object(x: unknown): Record<string, unknown> {
  require(!!x &&
    typeof x === "object" &&
    !Array.isArray(x), "Invalid case object");
  return x as Record<string, unknown>;
}
function keys(value: Record<string, unknown>, allowed: string[]) {
  require(Object.keys(value).sort().join() ===
    allowed.sort().join(), "Unexpected or missing manifest field");
}
export function parseManifest(value: unknown): Manifest {
  const m = object(value),
    d = object(m.decision),
    files = object(m.files),
    e = object(m.evidence);
  keys(m, [
    "version",
    "id",
    "title",
    "region",
    "basis",
    "initialSoc",
    "reserveSoc",
    "decision",
    "files",
    "attribution",
    "evidence",
  ]);
  keys(d, [
    "at",
    "stationId",
    "stationName",
    "targetSoc",
    "chargingSeconds",
    "minimumSocWithStop",
    "arrivalSocWithStop",
    "arrivalWorkingS",
    "waitSeconds",
    "energyKwh",
    "choices",
  ]);
  keys(e, ["kind", "prefixSha256", "sourceRevision"]);
  require(["branched-trip-demo/1.0.0", "branched-trip-demo/1.1.0"].includes(
    String(m.version),
  ), "Unsupported demo version");
  require([
    m.id,
    m.title,
    m.region,
    m.basis,
    d.stationId,
    d.stationName,
    e.kind,
  ].every(text), "Missing case identity");
  require(bounded(m.initialSoc, 0, 1) &&
    bounded(m.reserveSoc, 0, 1) &&
    (m.version === "branched-trip-demo/1.0.0"
      ? d.at === 20
      : Number.isSafeInteger(d.at) && bounded(d.at, 1, 900)) &&
    bounded(d.targetSoc, 0, 1) &&
    bounded(d.chargingSeconds) &&
    bounded(d.minimumSocWithStop, 0, 1) &&
    bounded(d.arrivalSocWithStop, 0, 1) &&
    bounded(d.arrivalWorkingS, Number(d.at)) &&
    bounded(d.waitSeconds) &&
    bounded(d.energyKwh, 0, 10000), "Invalid decision configuration");
  require(JSON.stringify(d.choices) ===
    '["charge","continue"]', "Expected exactly two decisions");
  require(Array.isArray(m.attribution) &&
    m.attribution.length > 0 &&
    m.attribution.every(text), "Missing attribution");
  require([e.prefixSha256, e.sourceRevision].every(
    (v) => typeof v === "string" && /^[a-f0-9]{64}$/.test(v),
  ), "Missing evidence fingerprints");
  require(Object.keys(files).sort().join() ===
    "catalog,charge,common,continue,map", "Unexpected case assets");
  let total = 0;
  for (const value of Object.values(files)) {
    const f = object(value);
    keys(f, ["path", "bytes", "sha256"]);
    require(typeof f.path === "string" &&
      /^[a-z][a-z0-9-]*\.json$/.test(f.path) &&
      Number.isSafeInteger(f.bytes) &&
      bounded(f.bytes, 1, 2_000_000) &&
      typeof f.sha256 === "string" &&
      /^[a-f0-9]{64}$/.test(f.sha256), "Invalid asset reference");
    total += f.bytes;
  }
  require(total <= MAX_BYTES, "Case exceeds loading budget");
  return value as Manifest;
}
export function parseTimeline(value: unknown, width: number): Timeline {
  const t = object(value);
  require([t.frames, t.forecasts, t.progress, t.events, t.transfers].every(
    Array.isArray,
  ), "Incomplete timeline");
  const timeline = value as Timeline;
  if (timeline.comparisons) {
    require(Array.isArray(timeline.comparisons) &&
      timeline.comparisons.length <= 1000, "Invalid comparison count");
    for (const c of timeline.comparisons)
      require(bounded(c.available) &&
        bounded(c.from) &&
        bounded(c.until, c.from) &&
        c.until <= c.available &&
        [c.predicted, c.observed, c.error].every(finite) &&
        Math.abs(c.error - (c.predicted - c.observed)) <
          1e-6, "Invalid recorded comparison");
  }
  require(timeline.frames.length <= 10000 &&
    timeline.forecasts.length <= 1000 &&
    timeline.progress.length <= 10000 &&
    timeline.events.length <= 1000 &&
    timeline.transfers.length <= 10000, "Timeline resource limit");
  let previous = -1;
  for (const f of timeline.frames) {
    require(Array.isArray(f) &&
      f.length >= 2 &&
      f.length <= 3 &&
      bounded(f[0]) &&
      f[0] > previous &&
      Array.isArray(f[1]) &&
      f[1].length === width &&
      f[1].every((v) => v === null || finite(v)) &&
      (!f[2] ||
        (Array.isArray(f[2]) &&
          f[2].length === width &&
          f[2].every((q) =>
            ["good", "invalid", "missing", "unknown"].includes(q),
          ))), "Invalid observation order or values");
    require(f[1].every((v, i) =>
      (f[2]?.[i] ?? "good") === "good" ? finite(v) : v === null,
    ), "Invalid observation quality");
    previous = f[0];
  }
  const seen = new Set<string>();
  for (const f of timeline.forecasts) {
    require(!!f &&
      text(f.id) &&
      !seen.has(f.id) &&
      bounded(f.available) &&
      bounded(f.cutoff) &&
      f.cutoff <= f.available &&
      [f.model, f.inputId, f.routeId, f.trigger].every(text) &&
      ["available", "unavailable", "infeasible"].includes(f.status) &&
      (f.reason === null ||
        text(f.reason)), "Invalid forecast provenance or availability");
    seen.add(f.id);
    if (f.summary) {
      const s = f.summary;
      require([
        s.minimum,
        s.duration,
        s.distance,
        s.energyKwh,
        s.drawKwh,
        s.recoveryKwh,
        s.auxKwh,
        s.capacityKwh,
      ].every(finite) &&
        bounded(s.minimum, 0, 1) &&
        (s.arrival === null || bounded(s.arrival, 0, 1)) &&
        s.duration >= 0 &&
        s.distance >= 0 &&
        s.capacityKwh > 0 &&
        Array.isArray(s.assumptions) &&
        s.assumptions.every(text), "Invalid forecast summary");
      if (s.ledger) {
        const l = s.ledger;
        require([
          "initial_energy_j",
          "capacity_j",
          "net_battery_j",
          "battery_draw_j",
          "battery_recovery_j",
          "auxiliary_required_j",
          "propulsion_required_j",
          "regeneration_used_j",
          "unserved_energy_j",
          "rejected_regeneration_j",
        ].every((key) => finite(l[key])) &&
          l.capacity_j > 0 &&
          typeof l.reserve_breached === "boolean" &&
          typeof l.motion_feasible === "boolean" &&
          Array.isArray(l.assumptions) &&
          l.assumptions.every(text), "Invalid recorded energy ledger");
        require(l.capacity_j / 3600000 === s.capacityKwh &&
          l.net_battery_j / 3600000 === s.energyKwh &&
          l.battery_draw_j / 3600000 === s.drawKwh &&
          l.battery_recovery_j / 3600000 === s.recoveryKwh &&
          l.auxiliary_required_j / 3600000 === s.auxKwh &&
          JSON.stringify(l.assumptions) ===
            JSON.stringify(
              s.assumptions,
            ), "Recorded energy ledger differs from summary");
      }
    } else require(f.status === "unavailable", "Missing forecast summary");
  }
  for (const p of timeline.progress)
    require(p &&
      bounded(p.t) &&
      (p.remainingM === null || bounded(p.remainingM, 0, 10_000_000)) &&
      typeof p.atStop === "boolean", "Invalid recorded progress");
  for (const event of timeline.events)
    require(event &&
      bounded(event.t) &&
      text(event.kind) &&
      text(event.title), "Invalid event");
  for (const transfer of timeline.transfers)
    require(transfer &&
      bounded(transfer.t) &&
      bounded(transfer.deliveredKwh, 0, 10000) &&
      [
        "approaching",
        "waiting_for_charger",
        "charging",
        "target_reached",
        "motion_resumed",
      ].includes(transfer.state), "Invalid measured transfer");
  for (const items of [timeline.progress, timeline.events, timeline.transfers])
    require(items.every(
      (v, i) => !i || v.t >= items[i - 1]!.t,
    ), "Event time moved backward");
  if (timeline.summary) {
    const s = timeline.summary;
    require(bounded(s.duration) &&
      bounded(s.arrivalSoc, 0, 1) &&
      bounded(s.minimumSoc, 0, 1) &&
      bounded(s.deliveredKwh, 0, 10000) &&
      (s.reserveCrossing === null ||
        bounded(s.reserveCrossing, 0, s.duration)) &&
      s.completed === true, "Invalid measured outcome");
  }
  return timeline;
}
export function validateCase(
  m: Manifest,
  assets: Record<string, unknown>,
): DemoCase {
  require(Array.isArray(assets.catalog) &&
    assets.catalog.length === 20, "Expected twenty recorded signals");
  const catalog = assets.catalog as Signal[];
  require(new Set(catalog.map((s) => s.id)).size === catalog.length &&
    catalog.every(
      (s) =>
        s &&
        text(s.id) &&
        text(s.unit) &&
        text(s.provenance) &&
        bounded(s.freshness, 0.1, 60),
    ), "Invalid signal catalog");
  for (const id of [
    "location.latitude",
    "location.longitude",
    "battery.pack.soc",
    "motion.speed",
  ])
    require(catalog.some((s) => s.id === id), "Required observation missing");
  const map = parseContext(assets.map, {
    run_id: "browser-demo",
    asset_id: "bus-001",
  });
  const common = parseTimeline(assets.common, catalog.length);
  const charge = parseTimeline(assets.charge, catalog.length);
  const continuing = parseTimeline(assets.continue, catalog.length);
  require((common.comparisons ?? []).every(
    (c) => c.available <= m.decision.at,
  ), "Future comparison in shared checkpoint");
  if (m.version === "branched-trip-demo/1.1.0")
    require([common, charge, continuing].every((t) =>
      t.forecasts.every((f) => !f.summary || !!f.summary.ledger),
    ), "Missing recorded energy ledger");
  require(common.frames.length > 0 &&
    common.frames.at(-1)![0] === m.decision.at &&
    common.frames.every((f) => f[0] <= m.decision.at) &&
    common.forecasts.every((f) => f.available <= m.decision.at) &&
    common.events.length === 0 &&
    common.transfers.length === 0, "Invalid shared checkpoint");
  require(common.frames.length +
    charge.frames.length +
    continuing.frames.length <=
    10000, "Combined frame limit");
  require(map.features.some(
    (f) =>
      f.properties.layer === "stations" &&
      f.properties.id === m.decision.stationId,
  ), "Decision station not on map");
  for (const branch of [charge, continuing]) {
    const end = branch.summary?.duration;
    require((branch.comparisons ?? []).every(
      (c) =>
        c.available > m.decision.at && end !== undefined && c.available <= end,
    ), "Invalid comparison branch join");
    require(end !== undefined &&
      branch.frames.length > 0 &&
      branch.frames[0]![0] > m.decision.at &&
      branch.frames.at(-1)![0] === end &&
      branch.events.every((e) => e.t >= m.decision.at && e.t <= end) &&
      branch.forecasts.every(
        (f) => f.available > m.decision.at && f.available <= end,
      ) &&
      branch.transfers.every((f) => f.t >= m.decision.at && f.t <= end) &&
      branch.progress.every(
        (f) => f.t > m.decision.at && f.t <= end,
      ), "Invalid branch join or ending");
    const soc = catalog.findIndex((s) => s.id === "battery.pack.soc");
    require(branch.frames.at(-1)![1][soc] ===
      branch.summary!.arrivalSoc, "Outcome differs from final observation");
  }
  require(!continuing.events.some((e) =>
    ["approval", "stop_inserted", "charging_observed"].includes(e.kind),
  ) &&
    continuing.transfers.length === 0 &&
    continuing.summary!.deliveredKwh === 0 &&
    charge.summary!.deliveredKwh > 0 &&
    [
      "approval",
      "stop_inserted",
      "charging_observed",
      "target_observed",
      "motion_resumed",
    ].every((k) =>
      charge.events.some((e) => e.kind === k),
    ), "Invalid decision effects");
  return { manifest: m, catalog, map, common, charge, continue: continuing };
}
export function csvCell(value: string | number | null): string {
  if (typeof value === "number") return String(value);
  let s = value ?? "";
  if (/^[\s]*[=+@-]/.test(s)) s = "'" + s;
  return `"${s.replaceAll('"', '""')}"`;
}
export async function readCase(
  manifestUrl: URL,
  signal?: AbortSignal,
): Promise<DemoCase> {
  async function bytes(url: URL, limit: number) {
    require(url.origin ===
      manifestUrl.origin, "Case assets must share the demo origin");
    const response = await fetch(url, {
      signal,
      credentials: "omit",
      redirect: "error",
    });
    require(response.ok, "Case file could not be loaded");
    const reader = response.body?.getReader();
    require(reader, "No case response body");
    let size = 0;
    const chunks: Uint8Array[] = [];
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.length;
      if (size > limit) {
        await reader.cancel();
        throw Error("Case response exceeds byte limit");
      }
      chunks.push(part.value);
    }
    const raw = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      raw.set(chunk, offset);
      offset += chunk.length;
    }
    return raw;
  }
  const m = parseManifest(
    JSON.parse(new TextDecoder().decode(await bytes(manifestUrl, 32000))),
  );
  const assets: Record<string, unknown> = {};
  await Promise.all(
    Object.entries(m.files).map(async ([name, f]) => {
      const raw = await bytes(new URL(f.path, manifestUrl), f.bytes);
      require(raw.length === f.bytes, "Case asset length changed");
      const hash = [
        ...new Uint8Array(await crypto.subtle.digest("SHA-256", raw)),
      ]
        .map((v) => v.toString(16).padStart(2, "0"))
        .join("");
      require(hash === f.sha256, "Case asset fingerprint changed");
      assets[name] = JSON.parse(new TextDecoder().decode(raw));
    }),
  );
  return validateCase(m, assets);
}

export type Player = {
  phase: "ready" | "intro" | "decision" | "outcome" | "finished";
  t: number;
  choice: Choice | null;
  playing: boolean;
  speed: number;
  generation: number;
};
export const initialPlayer = (): Player => ({
  phase: "ready",
  t: 0,
  choice: null,
  playing: false,
  speed: 1,
  generation: 0,
});
export type Action =
  | { type: "start" | "toggle" | "restart" | "other" | "exit" }
  | { type: "choose"; choice: Choice }
  | { type: "tick"; delta: number; hidden?: boolean }
  | { type: "seek"; t: number }
  | { type: "speed"; speed: number };
export function transition(p: Player, a: Action, data: DemoCase): Player {
  const fork = data.manifest.decision.at;
  const end = p.choice ? data[p.choice].summary!.duration : fork;
  if (a.type === "start" && p.phase === "ready")
    return { ...p, phase: "intro", playing: true };
  if (a.type === "restart")
    return {
      ...initialPlayer(),
      phase: "intro",
      playing: true,
      generation: p.generation + 1,
    };
  if (a.type === "exit")
    return { ...initialPlayer(), generation: p.generation + 1 };
  if (a.type === "other" && p.choice)
    return {
      ...initialPlayer(),
      phase: "decision",
      t: fork,
      generation: p.generation + 1,
    };
  if (
    a.type === "choose" &&
    p.phase === "decision" &&
    !p.choice &&
    ["charge", "continue"].includes(a.choice)
  )
    return {
      ...p,
      choice: a.choice,
      phase: "outcome",
      playing: true,
      generation: p.generation + 1,
    };
  if (a.type === "toggle" && ["intro", "outcome"].includes(p.phase))
    return { ...p, playing: !p.playing };
  if (
    a.type === "speed" &&
    p.choice &&
    [0.5, 1, 2, 5, 10, 20].includes(a.speed)
  )
    return { ...p, speed: a.speed };
  if (a.type === "seek" && p.choice && finite(a.t)) {
    const t = Math.max(0, Math.min(end, a.t));
    return {
      ...p,
      t,
      phase: t === end ? "finished" : "outcome",
      playing: false,
    };
  }
  if (
    a.type === "tick" &&
    p.playing &&
    !a.hidden &&
    finite(a.delta) &&
    a.delta > 0 &&
    a.delta <= 1
  ) {
    const t = Math.min(end, p.t + a.delta * (p.choice ? p.speed : 1));
    if (t >= end)
      return {
        ...p,
        t,
        playing: false,
        phase: p.choice ? "finished" : "decision",
      };
    return { ...p, t };
  }
  return p;
}
export function projectCase(data: DemoCase, player: Player) {
  const branch = player.choice ? data[player.choice] : null;
  const frames = [...data.common.frames, ...(branch?.frames ?? [])].filter(
    (f) => f[0] <= player.t,
  );
  const forecasts = [
    ...data.common.forecasts,
    ...(branch?.forecasts ?? []),
  ].filter((f) => f.available <= player.t);
  const events = (branch?.events ?? []).filter((e) => e.t <= player.t);
  const progress = [...data.common.progress, ...(branch?.progress ?? [])]
    .filter((p) => p.t <= player.t)
    .at(-1);
  const transfer = branch?.transfers.filter((e) => e.t <= player.t).at(-1);
  return {
    frames,
    current: frames.at(-1),
    forecasts,
    forecast: forecasts.at(-1),
    events,
    progress,
    transfer,
    comparisons: [
      ...(data.common.comparisons ?? []),
      ...(branch?.comparisons ?? []),
    ].filter((c) => c.available <= player.t),
  };
}

// Loaded cases are immutable. Only crossing an available-record boundary changes
// their causal projection; the playhead and reading ages keep their own clock.
export function createProjector(data: DemoCase) {
  const boundaries = new Map<Choice | null, number[]>();
  for (const choice of [null, "charge", "continue"] as const) {
    const timelines = choice ? [data.common, data[choice]] : [data.common];
    boundaries.set(
      choice,
      [
        ...new Set(
          timelines.flatMap((timeline) => [
            ...timeline.frames.map((frame) => frame[0]),
            ...timeline.forecasts.map((forecast) => forecast.available),
            ...timeline.progress.map((progress) => progress.t),
            ...timeline.events.map((event) => event.t),
            ...timeline.transfers.map((transfer) => transfer.t),
            ...(timeline.comparisons ?? []).map(
              (comparison) => comparison.available,
            ),
          ]),
        ),
      ].sort((a, b) => a - b),
    );
  }
  let previous:
    | {
        choice: Choice | null;
        reached: number;
        view: ReturnType<typeof projectCase>;
      }
    | undefined;
  return (player: Player) => {
    const times = boundaries.get(player.choice)!;
    let lo = 0,
      hi = times.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (times[mid]! <= player.t) lo = mid + 1;
      else hi = mid;
    }
    if (previous?.choice === player.choice && previous.reached === lo)
      return previous.view;
    const view = projectCase(data, player);
    previous = { choice: player.choice, reached: lo, view };
    return view;
  };
}

export function recordedActivity(
  events: DemoEvent[],
  kind: string,
  selected: number | null,
) {
  // The index identifies the original branch record, including simultaneous
  // events of the same kind. Filtering must not renumber those identities.
  const entries = events
    .map((event, id) => ({ event, id }))
    .filter(({ event }) => kind === "all" || event.kind === kind);
  return {
    entries,
    selected: entries.find(({ id }) => id === selected) ?? entries.at(-1),
  };
}
