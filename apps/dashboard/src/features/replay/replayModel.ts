import formatProfile from "../../../../../contracts/monitoring.profile.json" with { type: "json" };
import type { Point, Reading, Sample } from "../../monitoring/model.ts";
import { parseContext, coordinates } from "../map/mapModel.ts";
import type { Fix, MapContext } from "../map/mapModel.ts";

type Definition = Reading["definition"] & { source_id: string };
export interface Packet {
  event_id: string;
  run_id: string;
  asset_id: string;
  mode: string;
  source_id: string;
  source_epoch: number;
  stream_seq: number;
  session_id: string;
  sim_time_s: number;
  observed_at: string;
  manifest_sha256: string;
  schema_version: string;
  catalog_version: string;
  annotations?: Record<string, string>;
  samples: (Sample & { signal_id: string; unit: string })[];
}
export interface RecordPacket {
  wire_sha256: string;
  raw_base64: string;
  received_at: string;
  persisted_at: string;
  source_revision: number;
  seed: boolean;
  body: Packet;
}
export interface ReplaySession {
  package_version: string;
  mode: "replay";
  replay_run_id: string;
  namespace: string;
  source_run_id: string;
  asset_id: string;
  source_cursor: string;
  created_at: string;
  from_s: number;
  to_s: number;
  time_basis: string;
  policy_sha256: string;
  catalog: {
    catalog_version: string;
    source_catalog_sha256: string;
    signals: Record<string, Definition>;
  };
  recipes: { sha256: string; raw_base64: string }[];
  packets: RecordPacket[];
  map_context: { sha256: string; raw_base64: string } | null;
  context: MapContext | null;
  digest: string;
}
const obj = (x: unknown): x is Record<string, unknown> =>
  !!x && typeof x === "object" && !Array.isArray(x);
const finite = (x: unknown): x is number =>
  typeof x === "number" && Number.isFinite(x);
const integer = (x: unknown): x is number =>
  finite(x) && Number.isSafeInteger(x) && x >= 0;
const date = (x: unknown) =>
  typeof x === "string" && Number.isFinite(Date.parse(x));
const hash = (x: unknown): x is string =>
  typeof x === "string" && /^[a-f0-9]{64}$/.test(x);
const uuid = (x: unknown): x is string =>
  typeof x === "string" &&
  /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(x);
function requireValue(ok: unknown, message: string): asserts ok {
  if (!ok) throw Error(message);
}
export async function sha256(raw: Uint8Array) {
  const copy = new Uint8Array(raw);
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", copy))]
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("");
}
function freeze<T>(x: T): T {
  if (x && typeof x === "object" && !Object.isFrozen(x)) {
    Object.freeze(x);
    for (const v of Object.values(x)) freeze(v);
  }
  return x;
}
async function artifact(x: unknown) {
  requireValue(
    obj(x) && hash(x.sha256) && typeof x.raw_base64 === "string",
    "Invalid artifact envelope",
  );
  const raw = Uint8Array.from(atob(x.raw_base64), (c) => c.charCodeAt(0));
  requireValue(
    (await sha256(raw)) === x.sha256,
    "Artifact bytes failed SHA-256 verification",
  );
  return JSON.parse(
    new TextDecoder("utf-8", { fatal: true }).decode(raw),
  ) as Record<string, unknown>;
}
export async function verifySession(
  raw: Uint8Array,
  digest: string,
  expected: { asset: string; run: string; from: number; to: number },
): Promise<ReplaySession> {
  requireValue(
    raw.byteLength <= 16 * 1024 * 1024 &&
      hash(digest) &&
      (await sha256(raw)) === digest,
    "Replay response hash or size mismatch",
  );
  const p: unknown = JSON.parse(
    new TextDecoder("utf-8", { fatal: true }).decode(raw),
  );
  requireValue(
    obj(p) &&
      p.package_version === "1.0.0" &&
      p.mode === "replay" &&
      p.source_mode === "simulation" &&
      uuid(p.replay_run_id) &&
      p.namespace === "replay/" + p.replay_run_id &&
      p.source_run_id === expected.run &&
      p.asset_id === expected.asset &&
      p.from_s === expected.from &&
      p.to_s === expected.to &&
      finite(p.from_s) &&
      finite(p.to_s) &&
      p.from_s >= 0 &&
      p.to_s >= p.from_s &&
      p.to_s - p.from_s <= 86400 &&
      date(p.created_at) &&
      hash(p.policy_sha256) &&
      p.time_basis === "retrospective recorded simulation time",
    "Replay identity, version or clock mismatch",
  );
  requireValue(
    obj(p.capabilities) &&
      p.capabilities.dispatch === false &&
      p.capabilities.publish_observations === false &&
      p.capabilities.write_source_state === false,
    "Replay capability mismatch",
  );
  const prefix = `${expected.run}:${expected.asset}:`;
  requireValue(
    typeof p.source_cursor === "string" &&
      p.source_cursor.startsWith(prefix) &&
      /^\d+$/.test(p.source_cursor.slice(prefix.length)),
    "Invalid source revision",
  );
  const revision = Number(p.source_cursor.slice(prefix.length));
  requireValue(
    integer(revision) &&
      obj(p.catalog) &&
      typeof p.catalog.catalog_version === "string" &&
      hash(p.catalog.source_catalog_sha256) &&
      obj(p.catalog.signals),
    "Invalid source catalog",
  );
  const format = (
    formatProfile.operational_formats as Record<
      string,
      { schema_version: string; manifest_version: string }
    >
  )[p.catalog.catalog_version];
  requireValue(format, "Unsupported registered source format");
  const definitions = p.catalog.signals;
  requireValue(
    Object.keys(definitions).length > 0 &&
      Object.keys(definitions).length <= 5000,
    "Invalid signal count",
  );
  for (const d of Object.values(definitions))
    requireValue(
      obj(d) &&
        typeof d.unit === "string" &&
        typeof d.source_id === "string" &&
        typeof d.component_id === "string" &&
        typeof d.physical_location === "string" &&
        typeof d.provenance === "string" &&
        finite(d.sample_period_s) &&
        d.sample_period_s > 0 &&
        finite(d.freshness_s) &&
        d.freshness_s > 0,
      "Invalid registered signal definition",
    );
  requireValue(
    Array.isArray(p.recipes) &&
      p.recipes.length > 0 &&
      p.recipes.length <= 10000,
    "Invalid recipes",
  );
  const recipes = new Map<string, Record<string, unknown>>();
  for (const envelope of p.recipes) {
    const r = await artifact(envelope);
    requireValue(
      r.manifest_version === format.manifest_version &&
        obj(r.identity) &&
        r.identity.run_id === expected.run &&
        r.identity.asset_id === expected.asset &&
        r.mode === "simulation",
      "Recipe identity/version mismatch",
    );
    recipes.set(envelope.sha256, r);
  }
  requireValue(
    Array.isArray(p.packets) &&
      p.packets.length > 0 &&
      p.packets.length <= 10000,
    "Invalid packet count",
  );
  const packets: RecordPacket[] = [];
  const events = new Set<string>();
  let bytes = 0;
  for (const envelope of p.packets) {
    requireValue(
      obj(envelope) &&
        hash(envelope.wire_sha256) &&
        typeof envelope.raw_base64 === "string" &&
        date(envelope.received_at) &&
        date(envelope.persisted_at) &&
        integer(envelope.source_revision) &&
        envelope.source_revision <= revision &&
        typeof envelope.seed === "boolean",
      "Invalid packet envelope",
    );
    bytes += atob(envelope.raw_base64).length;
    requireValue(
      bytes <= 8 * 1024 * 1024,
      "Original packet byte limit exceeded",
    );
    const b = await artifact({
      sha256: envelope.wire_sha256,
      raw_base64: envelope.raw_base64,
    });
    requireValue(
      b.run_id === expected.run &&
        b.asset_id === expected.asset &&
        b.mode === "simulation" &&
        uuid(b.event_id) &&
        !events.has(b.event_id) &&
        uuid(b.session_id) &&
        integer(b.source_epoch) &&
        integer(b.stream_seq) &&
        finite(b.sim_time_s) &&
        b.sim_time_s >= 0 &&
        b.sim_time_s <= p.to_s &&
        envelope.seed === b.sim_time_s < p.from_s &&
        date(b.observed_at) &&
        typeof b.source_id === "string" &&
        b.schema_version === format.schema_version &&
        b.catalog_version === p.catalog.catalog_version &&
        hash(b.manifest_sha256) &&
        Array.isArray(b.samples) &&
        b.samples.length > 0 &&
        b.samples.length <= 5000,
      "Packet identity, bounds or version mismatch",
    );
    const recipe = recipes.get(b.manifest_sha256),
      identity = recipe?.identity;
    requireValue(
      obj(identity) &&
        identity.source_id === b.source_id &&
        identity.source_epoch === b.source_epoch &&
        identity.session_id === b.session_id &&
        recipe?.schema_version === b.schema_version &&
        recipe.catalog_version === b.catalog_version,
      "Packet recipe mismatch",
    );
    const seen = new Set<string>();
    for (const s of b.samples) {
      requireValue(
        obj(s) &&
          typeof s.signal_id === "string" &&
          !seen.has(s.signal_id) &&
          obj(definitions[s.signal_id]),
        "Unregistered or duplicate signal",
      );
      const d = definitions[s.signal_id];
      requireValue(obj(d), "Missing signal definition");
      requireValue(
        d.source_id === b.source_id &&
          s.unit === d.unit &&
          typeof s.provenance === "string" &&
          ["good", "missing", "invalid"].includes(String(s.quality)) &&
          (s.quality === "good" ? finite(s.value) : s.value === null) &&
          (s.raw_value === undefined || finite(s.raw_value)) &&
          (s.reason === undefined || typeof s.reason === "string"),
        "Invalid recorded sample",
      );
      seen.add(s.signal_id);
    }
    events.add(b.event_id);
    packets.push({ ...envelope, body: b } as unknown as RecordPacket);
  }
  packets.sort(
    (a, b) =>
      a.body.sim_time_s - b.body.sim_time_s ||
      a.body.source_epoch - b.body.source_epoch ||
      a.body.stream_seq - b.body.stream_seq ||
      a.body.event_id.localeCompare(b.body.event_id),
  );
  let context: MapContext | null = null;
  if (p.map_context !== null) {
    context = parseContext(await artifact(p.map_context), {
      run_id: expected.run,
      asset_id: expected.asset,
    });
  }
  return freeze({ ...p, packets, context, digest } as unknown as ReplaySession);
}
export function visiblePackets(s: ReplaySession, t: number) {
  return s.packets.filter((p) => p.body.sim_time_s <= t);
}
export function project(s: ReplaySession, t: number) {
  const current = new Map<
    string,
    { packet: RecordPacket; sample: Packet["samples"][number] }
  >();
  for (const packet of visiblePackets(s, t))
    for (const sample of packet.body.samples)
      current.set(sample.signal_id, { packet, sample });
  return Object.entries(s.catalog.signals).map(([signal_id, definition]) => {
    const entry = current.get(signal_id),
      age = entry ? t - entry.packet.body.sim_time_s : null;
    return {
      signal_id,
      definition,
      entry,
      age,
      quality: entry?.sample.quality ?? "unknown",
      recency:
        age === null
          ? "unavailable"
          : age <= definition.freshness_s
            ? "recent in replay"
            : "stale in replay",
    };
  });
}
export function history(s: ReplaySession, signal: string, t: number): Point[] {
  return visiblePackets(s, t)
    .filter((p) => p.body.sim_time_s >= s.from_s)
    .flatMap((p) =>
      p.body.samples
        .filter((x) => x.signal_id === signal)
        .map((sample) => ({
          event_id: p.body.event_id,
          sim_time_s: p.body.sim_time_s,
          observed_at: p.body.observed_at,
          received_at: p.received_at,
          source_id: p.body.source_id,
          source_epoch: p.body.source_epoch,
          stream_seq: p.body.stream_seq,
          sample,
        })),
    );
}
export function fixes(s: ReplaySession, t: number): Fix[] {
  return visiblePackets(s, t).flatMap((p) => {
    const b = p.body,
      a = b.samples.find((x) => x.signal_id === "location.latitude"),
      o = b.samples.find((x) => x.signal_id === "location.longitude");
    if (!a && !o) return [];
    const xy = [o?.value, a?.value];
    return [
      {
        event_id: b.event_id,
        sim_time_s: b.sim_time_s,
        observed_at: b.observed_at,
        source_id: b.source_id,
        source_epoch: b.source_epoch,
        coordinates:
          a?.quality === "good" &&
          o?.quality === "good" &&
          a.unit === "deg" &&
          o.unit === "deg" &&
          coordinates(xy)
            ? xy
            : null,
        quality:
          a?.quality === "good" && o?.quality === "good"
            ? "good"
            : "unavailable",
        reason: null,
      },
    ];
  });
}
export function step(s: ReplaySession, t: number, direction: 1 | -1) {
  const times = [
    ...new Set(
      s.packets.map((p) => p.body.sim_time_s).filter((x) => x >= s.from_s),
    ),
  ];
  return direction === 1
    ? (times.find((x) => x > t) ?? s.to_s)
    : (times.filter((x) => x < t).at(-1) ?? s.from_s);
}
export function advance(
  t: number,
  elapsedMs: number,
  rate: number,
  end: number,
) {
  return {
    time:
      elapsedMs < 0 || elapsedMs > 1000
        ? t
        : Math.min(end, t + (elapsedMs / 1000) * rate),
    interrupted: elapsedMs < 0 || elapsedMs > 1000,
  };
}
