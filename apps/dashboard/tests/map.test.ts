import { test } from "node:test";
import assert from "node:assert/strict";
import {
  chargingMarkers,
  coordinates,
  currentPosition,
  mergeTrajectory,
  pathSegments,
} from "../src/features/map/mapModel.ts";
import type { Fix, MapContext } from "../src/features/map/mapModel.ts";
import type { Snapshot } from "../src/monitoring/model.ts";
const fix = (
  t: number,
  coordinates: Fix["coordinates"] = [-73.57, 45.51],
): Fix => ({
  event_id: String(t),
  sim_time_s: t,
  observed_at: "2026-11-03T00:00:00Z",
  source_id: "s",
  source_epoch: 1,
  coordinates,
  quality: coordinates ? "good" : "unavailable",
  reason: null,
});
test("map validates longitude/latitude bounds and retains legitimate zero", () => {
  assert.ok(coordinates([0, 0]));
  assert.ok(coordinates([-73.57, 45.51]));
  assert.ok(!coordinates([181, 45]));
  assert.ok(!coordinates([-73.57, NaN]));
});
test("full-trip deltas merge late, invalid, and duplicate fixes in working-time order", () => {
  const old = [fix(1), fix(3500)];
  assert.equal(mergeTrajectory(old, []), old);
  const merged = mergeTrajectory(old, [fix(3501), fix(2, null), fix(3500)]);
  assert.deepEqual(
    merged.map(({ sim_time_s }) => sim_time_s),
    [1, 2, 3500, 3501],
  );
  assert.deepEqual(
    pathSegments(merged, 5).map((part) =>
      part.map((point) => point.sim_time_s),
    ),
    [[1], [3500, 3501]],
  );
});
test("charging overlays use mapped bay IDs and never duplicate an approved bay", () => {
  const context = {
    features: ["a", "b"].map((id, index) => ({
      type: "Feature",
      properties: { id, layer: "stations", name: id },
      geometry: { type: "Point", coordinates: [-73.5, 45.5 + index / 10] },
    })),
  } as MapContext;
  const result = chargingMarkers(
    context,
    { preferred: "a", fallback: "b", approved: "a" },
    { coordinates: [-73.51, 45.55], working_s: 650 },
  );
  assert.deepEqual(
    result.map(({ kind }) => kind),
    ["approved", "fallback", "reserve"],
  );
  assert.deepEqual(result[1]?.coordinates, [-73.5, 45.6]);
  assert.deepEqual(
    chargingMarkers(
      context,
      { preferred: "unknown" },
      { coordinates: [-181, 45], working_s: 5 },
    ),
    [],
  );
});
test("missing navigation, cadence and source epoch changes break the observed path", () => {
  const points = [
    fix(0),
    fix(1),
    fix(2, null),
    fix(3),
    fix(20),
    { ...fix(21), source_epoch: 2 },
  ];
  assert.deepEqual(
    pathSegments(points, 5).map((s) => s.map((p) => p.sim_time_s)),
    [[0, 1], [3], [20], [21]],
  );
});
test("current location must be a same-event pair and disconnected display stays stale", () => {
  const sample = (signal_id: string, value: number) => ({
    signal_id,
    source_id: "s",
    quality: "good",
    freshness: "fresh",
    definition: { unit: "deg" },
    observation: {
      event_id: "event",
      sim_time_s: 1,
      observed_at: "2026-11-03T00:00:00Z",
      sample: { value },
    },
  });
  const snap = {
    items: [sample("location.latitude", 0), sample("location.longitude", 0)],
  } as Snapshot;
  assert.deepEqual(currentPosition(snap, true)?.coordinates, [0, 0]);
  assert.equal(currentPosition(snap, false)?.freshness, "stale");
  snap.items[1]!.observation!.event_id = "other";
  assert.equal(currentPosition(snap, true), null);
  snap.items[1]!.observation!.event_id = "event";
  snap.items[0]!.quality = "invalid";
  assert.equal(currentPosition(snap, true), null);
});
