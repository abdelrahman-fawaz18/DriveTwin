import type { Snapshot } from "../../monitoring/model.ts";
import type { FeatureCollection, Geometry } from "geojson";
import { chargingBayName } from "../trip/tripReview.ts";

export type MapContext = FeatureCollection<
  Geometry,
  {
    id: string;
    layer: string;
    name: string;
    hypothetical?: boolean;
    power_limit_W?: number;
    road_access?: string;
    duration_s?: number;
  }
> & {
  context_version: string;
  run_id: string;
  asset_id: string;
  mission_id: string;
  bbox: [number, number, number, number];
  attribution: string;
  input_sha256: Record<string, string>;
};
export interface Fix {
  event_id: string;
  sim_time_s: number;
  observed_at: string;
  source_id: string;
  source_epoch: number;
  coordinates: [number, number] | null;
  quality: string;
  reason: string | null;
}
export interface Trajectory {
  run_id: string;
  asset_id: string;
  as_of: string;
  max_gap_s: number;
  items: Fix[];
  next: string | null;
}

export function passengerStopLabel(
  context: MapContext | null | undefined,
  id: string | null,
) {
  if (id === null) return "No passenger stops remaining";
  return (
    context?.features.find(
      ({ properties }) => properties.layer === "stops" && properties.id === id,
    )?.properties.name ?? "Unavailable"
  );
}

export function mergeTrajectory(previous: Fix[], updates: Fix[]) {
  if (!updates.length) return previous;
  const points = new Map(previous.map((point) => [point.event_id, point]));
  for (const point of updates) points.set(point.event_id, point);
  return [...points.values()].sort(
    (a, b) =>
      a.sim_time_s - b.sim_time_s || a.event_id.localeCompare(b.event_id),
  );
}

export type ChargingMarker = {
  kind: "preferred" | "fallback" | "approved" | "reserve" | "review";
  coordinates: [number, number];
  label: string;
  bay?: string;
};

export function chargingMarkers(
  context: MapContext | null,
  roles: {
    preferred?: string;
    fallback?: string;
    approved?: string;
    review?: string;
  },
  crossing?: { coordinates: [number, number]; working_s: number },
): ChargingMarker[] {
  if (!context) return [];
  const markers: ChargingMarker[] = [];
  const stationLabels = {
    preferred: "Recommended charging bay",
    fallback: "Fallback charging bay",
    approved: "Approved charging bay",
    review: "Charging bay under review",
  } as const;
  for (const kind of ["approved", "preferred", "fallback", "review"] as const) {
    const bay = roles[kind];
    if (!bay || (kind !== "approved" && bay === roles.approved)) continue;
    if (
      kind === "review" &&
      (bay === roles.preferred || bay === roles.fallback)
    )
      continue;
    const station = context.features.find(
      (feature) =>
        feature.properties.layer === "stations" &&
        feature.properties.id === bay,
    );
    if (
      station?.geometry.type === "Point" &&
      coordinates(station.geometry.coordinates)
    ) {
      markers.push({
        kind,
        bay,
        coordinates: station.geometry.coordinates,
        label: `${stationLabels[kind]} · ${chargingBayName(bay)}`,
      });
    }
  }
  if (
    crossing &&
    coordinates(crossing.coordinates) &&
    Number.isFinite(crossing.working_s) &&
    crossing.working_s >= 0
  ) {
    markers.push({
      kind: "reserve",
      coordinates: crossing.coordinates,
      label: `Predicted reserve crossing without charging · working second ${Math.round(crossing.working_s)}`,
    });
  }
  return markers;
}

export function coordinates(x: unknown): x is [number, number] {
  return (
    Array.isArray(x) &&
    x.length === 2 &&
    typeof x[0] === "number" &&
    Number.isFinite(x[0]) &&
    Math.abs(x[0]) <= 180 &&
    typeof x[1] === "number" &&
    Number.isFinite(x[1]) &&
    Math.abs(x[1]) <= 90
  );
}
export function parseContext(
  x: unknown,
  snap: Pick<Snapshot, "run_id" | "asset_id">,
): MapContext {
  const c = x as MapContext;
  if (
    !c ||
    c.context_version !== "1.0.0" ||
    c.type !== "FeatureCollection" ||
    c.run_id !== snap.run_id ||
    c.asset_id !== snap.asset_id ||
    !Array.isArray(c.features) ||
    c.features.length > 10000 ||
    !Array.isArray(c.bbox) ||
    c.bbox.length !== 4 ||
    !c.bbox.every(Number.isFinite) ||
    typeof c.attribution !== "string"
  )
    throw Error("Map context identity or format changed");
  let count = 0;
  for (const f of c.features) {
    if (
      f.type !== "Feature" ||
      !f.properties ||
      !["roads", "route", "stops", "stations", "endpoints", "detour"].includes(
        f.properties.layer,
      ) ||
      typeof f.properties.name !== "string" ||
      !f.geometry ||
      !["Point", "LineString", "MultiLineString"].includes(f.geometry.type)
    )
      throw Error("Invalid map feature");
    const g = f.geometry;
    const lines =
      g.type === "Point"
        ? [[g.coordinates]]
        : g.type === "LineString"
          ? [g.coordinates]
          : g.type === "MultiLineString"
            ? g.coordinates
            : [];
    for (const line of lines) {
      if (!Array.isArray(line) || !line.every(coordinates))
        throw Error("Invalid map coordinate order or bounds");
      count += line.length;
    }
  }
  if (count > 50000) throw Error("Map context exceeds point limit");
  return c;
}
export function parseTrajectory(
  x: unknown,
  snap: Snapshot,
  anchor: string,
): Trajectory {
  const t = x as Trajectory;
  if (
    !t ||
    t.run_id !== snap.run_id ||
    t.asset_id !== snap.asset_id ||
    t.as_of !== anchor ||
    !Array.isArray(t.items) ||
    t.items.length > 1000 ||
    !(t.next === null || typeof t.next === "string") ||
    !Number.isFinite(t.max_gap_s)
  )
    throw Error("Trajectory identity changed");
  for (const f of t.items)
    if (
      !f ||
      !Number.isFinite(f.sim_time_s) ||
      !Number.isSafeInteger(f.source_epoch) ||
      typeof f.source_id !== "string" ||
      typeof f.event_id !== "string" ||
      !Number.isFinite(Date.parse(f.observed_at)) ||
      !(f.coordinates === null || coordinates(f.coordinates))
    )
      throw Error("Invalid trajectory point");
  return t;
}
export function pathSegments(points: Fix[], maxGap: number): Fix[][] {
  const result: Fix[][] = [];
  let segment: Fix[] = [];
  for (const p of points) {
    const prev = segment.at(-1);
    if (!p.coordinates) {
      if (segment.length) result.push(segment);
      segment = [];
      continue;
    }
    if (
      prev &&
      (p.sim_time_s - prev.sim_time_s > maxGap ||
        p.sim_time_s <= prev.sim_time_s ||
        p.source_epoch !== prev.source_epoch ||
        p.source_id !== prev.source_id)
    ) {
      result.push(segment);
      segment = [];
    }
    segment.push(p);
  }
  if (segment.length) result.push(segment);
  return result;
}
export function currentPosition(snap: Snapshot, connected: boolean) {
  const lat = snap.items.find((i) => i.signal_id === "location.latitude"),
    lon = snap.items.find((i) => i.signal_id === "location.longitude");
  const a = lat?.observation,
    b = lon?.observation;
  if (
    !a ||
    !b ||
    a.event_id !== b.event_id ||
    lat?.quality !== "good" ||
    lon?.quality !== "good" ||
    lat.definition.unit !== "deg" ||
    lon.definition.unit !== "deg" ||
    !coordinates([b.sample.value, a.sample.value])
  )
    return null;
  const accuracy = snap.items.find(
    (i) => i.signal_id === "location.horizontal_accuracy",
  );
  return {
    coordinates: [b.sample.value!, a.sample.value!] as [number, number],
    observed_at: a.observed_at,
    sim_time_s: a.sim_time_s,
    freshness: !connected
      ? "stale"
      : lat.freshness === "fresh" && lon.freshness === "fresh"
        ? "fresh"
        : lat.freshness === "paused" && lon.freshness === "paused"
          ? "paused"
          : "stale",
    accuracy:
      accuracy?.quality === "good" &&
      accuracy.observation?.event_id === a.event_id
        ? accuracy.observation.sample.value
        : null,
  };
}
