import assert from "node:assert/strict";
import { test } from "node:test";
import {
  Frames,
  segments,
  parseSnapshot,
  time,
  value,
} from "../src/monitoring/model.ts";
import {
  summaryCards,
  summaryReading,
} from "../src/features/overview/overview.ts";
import type { Reading } from "../src/monitoring/model.ts";
import type { Point } from "../src/monitoring/model.ts";
const point = (t: number, v: number | null, quality = "good"): Point => ({
  event_id: String(t),
  sim_time_s: t,
  observed_at: "2026-11-03T00:00:00Z",
  received_at: "2026-11-03T00:00:00Z",
  source_id: "s",
  source_epoch: 1,
  stream_seq: t + 1,
  sample: {
    value: v,
    quality: quality as "good",
    provenance: "simulated-observation",
  },
});

test("reused formatters preserve prior number precision, missing values and UTC display", () => {
  for (const n of [
    null,
    undefined,
    0,
    -0,
    1.234567,
    -98765.4321,
    1e20,
    Number.NaN,
    Infinity,
    -Infinity,
  ]) {
    const expected =
      n == null
        ? "—"
        : new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 }).format(
            n,
          );
    assert.equal(value(n), expected);
  }
  for (const iso of [
    undefined,
    "",
    "invalid",
    "2026-11-04T00:00:00Z",
    "2026-11-04T12:34:56.999Z",
    "2026-11-04T23:59:59Z",
    "2026-11-04T12:34:56+03:00",
    "2024-02-29T01:02:03-05:00",
  ]) {
    const expected = iso
      ? new Date(iso).toLocaleTimeString("en-US", {
          hour12: false,
          timeZone: "UTC",
        }) + " UTC"
      : "Not observed";
    assert.equal(time(iso), expected);
  }
});
test("zero remains a point; missing, invalid, cadence and epoch boundaries split paths", () => {
  const p = [
    point(0, 0),
    point(1, 1),
    point(2, null, "missing"),
    point(3, 2),
    point(4, null, "invalid"),
    point(5, 2),
    point(12, 3),
    { ...point(13, 4), source_epoch: 2 },
  ];
  assert.deepEqual(
    segments(p, 5).map((s) => s.map((p) => p.sim_time_s)),
    [[0, 1], [3], [5], [12], [13]],
  );
});
test("bounded rendering retains positive and negative spikes and endpoints", () => {
  const points = Array.from({ length: 1000 }, (_, i) =>
    point(i, i === 91 ? 800 : i === 92 ? -700 : 0),
  );
  const reduced = segments(points, 5, 100)[0]!;
  assert.ok(reduced.length <= 100);
  assert.equal(reduced[0]!.sim_time_s, 0);
  assert.equal(reduced.at(-1)!.sim_time_s, 999);
  assert.ok(reduced.some((p) => p.sample.value === 800));
  assert.ok(reduced.some((p) => p.sample.value === -700));
});
test("SSE handles fragmented messages and rejects unbounded input", () => {
  const f = new Frames();
  assert.deepEqual(f.push('event: snapshot\ndata: {"a"'), []);
  assert.deepEqual(f.push(':0}\n\ndata: {"b":1}\n\n'), ['{"a":0}', '{"b":1}']);
  assert.throws(() => f.push("x".repeat(2_000_001)));
});
test("snapshot rejects wrong asset, run, cursor and unsupported mode", () => {
  const s = {
    version: "1.0.0",
    run_id: "run",
    asset_id: "bus-001",
    mode: "simulation",
    as_of: "2026-11-03T00:00:00Z",
    cursor: "run:bus-001:0",
    items: [],
  };
  assert.equal(parseSnapshot(s, "bus-001").items.length, 0);
  for (const change of [
    { asset_id: "bus-002" },
    { run_id: "other" },
    { cursor: "run:bus-002:1" },
    { cursor: "run:bus-001:9007199254740992" },
    { mode: "replay" },
  ])
    assert.throws(() => parseSnapshot({ ...s, ...change }, "bus-001", "run"));
});
test("summary conversions require declared units and never turn null into zero", () => {
  const reading = (unit: string, value: number | null) =>
    ({ definition: { unit }, observation: { sample: { value } } }) as Reading;
  for (const [index, raw, expected] of [
    [0, 0.8, 80],
    [1, 10, 36],
    [2, 3_600_000, 1],
    [3, 300, 26.85],
  ]) {
    const card = summaryCards[index!]!;
    assert.ok(
      Math.abs(
        summaryReading(card, reading(card.stored, raw!)).value! - expected!,
      ) < 1e-9,
    );
    assert.equal(summaryReading(card, reading(card.stored, null)).value, null);
    assert.equal(summaryReading(card, reading("wrong-unit", raw!)).value, null);
  }
  assert.equal(summaryReading(summaryCards[0], reading("1", 0)).value, 0);
});
