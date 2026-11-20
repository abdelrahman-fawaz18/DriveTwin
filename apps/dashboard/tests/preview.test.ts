import assert from "node:assert/strict";
import test from "node:test";
import catalog from "../../../catalog/monitoring-signals.json" with { type: "json" };
import {
  csvCell,
  groups,
  observationTime,
  sampleValue,
  series,
  signals,
  statistics,
} from "../src/preview/data.ts";
import { routePosition } from "../src/preview/route.ts";

test("preview exposes the complete standard catalog exactly once", () => {
  assert.equal(signals.length, 20);
  assert.deepEqual(
    signals.map((s) => s.id).sort(),
    Object.keys(catalog.signals).sort(),
  );
  assert.deepEqual(
    groups.flatMap((g) => g.ids).sort(),
    signals.map((s) => s.id).sort(),
  );
  for (const signal of signals) {
    assert.ok(Number.isFinite(sampleValue(signal.id, 120)));
    assert.equal(signal.period, 1);
  }
});

test("map coordinates and displayed readings share one fixture", () => {
  for (const t of [0, 120, 300, 599, 601]) {
    assert.deepEqual(routePosition(t), [
      sampleValue("location.latitude", t),
      sampleValue("location.longitude", t),
    ]);
    assert.equal(
      sampleValue("battery.pack.power", t),
      sampleValue("battery.pack.voltage", t) *
        sampleValue("battery.pack.current", t),
    );
    assert.equal(
      sampleValue("battery.pack.energy", t),
      sampleValue("battery.pack.soc", t) * 300 * 3600000,
    );
  }
});

test("history keeps gaps and excludes them from numerical summaries", () => {
  const points = series("motion.speed", 89, 96, "bus-001", true);
  assert.equal(points.length, 8);
  assert.equal(statistics(points).missing, 6);
  assert.equal(statistics(points).count, 2);
  assert.equal(statistics(points.slice(1, -1)).mean, null);
  assert.equal(series("motion.speed", 96, 89, "bus-001").length, 0);
  assert.notEqual(observationTime(120), observationTime(120.2));
});

test("CSV escapes formulas and quotes while preserving signed numbers", () => {
  assert.equal(csvCell("=1+1"), '"\'=1+1"');
  assert.equal(csvCell('A"B'), '"A""B"');
  assert.equal(csvCell(-73.5), '"-73.5"');
  assert.equal(csvCell(null), '""');
});
