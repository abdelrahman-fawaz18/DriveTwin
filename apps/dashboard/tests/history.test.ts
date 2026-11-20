import assert from "node:assert/strict";
import { test } from "node:test";
import {
  exportCsv,
  parseExportPage,
  parsePlot,
  parseSummary,
  plotSegments,
} from "../src/features/history/historyModel.ts";
import type {
  HistoryQuery,
  PlotPoint,
} from "../src/features/history/historyModel.ts";
import type { Snapshot } from "../src/monitoring/model.ts";

const signal = "motion.speed";
const definition = {
  unit: "m/s",
  component_id: "motion",
  classification: "observed",
  sample_period_s: 1,
};
const query: HistoryQuery = {
  snap: {
    run_id: "run",
    asset_id: "bus-001",
    cursor: "run:bus-001:50",
    items: [{ signal_id: signal, source_id: "source", definition }],
  } as Snapshot,
  signals: [signal],
  bounds: { from: 0, to: 100 },
};
const meta = {
  mode: "simulation",
  run_id: "run",
  asset_id: "bus-001",
  as_of: query.snap.cursor,
  from_s: 0,
  to_s: 100,
  time_basis: "simulation seconds",
  policy_sha256: "a".repeat(64),
  catalog_version: "1.2.0",
  signals: { [signal]: definition },
  timings: { [signal]: { source_id: "source", period_s: 1 } },
};
function point(t: number, v: number, id: number, segment = 1): PlotPoint {
  return {
    signal_id: signal,
    sim_time_s: t,
    event_id: `00000000-0000-0000-0000-${String(id).padStart(12, "0")}`,
    source_id: "source",
    source_epoch: 1,
    stream_seq: id - 1,
    observed_at: "2026-11-04T00:00:00Z",
    received_at: "2026-11-04T00:00:01Z",
    sample: {
      value: v,
      quality: "good",
      provenance: "simulated-observation",
      unit: "m/s",
      signal_id: signal,
    },
    segment_id: `source:1:${segment}`,
  } as PlotPoint;
}
const bucket = {
  signal_id: signal,
  from_s: 0,
  to_s: 100,
  count: 10,
  good: 8,
  missing: 1,
  invalid: 1,
  continuity_breaks: 2,
  minimum: 0,
  maximum: 20,
  mean: 5,
  first_good_s: 0,
  last_good_s: 100,
};
function plot() {
  return {
    metadata: {
      ...structuredClone(meta),
      plot_version: "history-plot/1.0.0",
      resolution:
        "first/minimum/maximum/last per bucket and continuous segment",
      interpolated: false,
      point_limit: 4,
      point_count: 4,
      bucket_s: 100,
      quality_counts: {
        count: 10,
        good: 8,
        missing: 1,
        invalid: 1,
        continuity_breaks: 2,
      },
    },
    items: [
      point(0, 0, 1),
      point(30, 20, 2),
      point(60, 4, 3, 2),
      point(100, 5, 4, 2),
    ],
    buckets: [structuredClone(bucket)],
  };
}
test("bounded original points preserve zeros, extrema and explicit continuity across omitted good readings", () => {
  const parsed = parsePlot(plot(), query, 4);
  const runs = plotSegments(parsed.items);
  assert.deepEqual(
    runs.map((r) => r.map((p) => p.sim_time_s)),
    [
      [0, 30],
      [60, 100],
    ],
  );
  assert.equal(runs[0]![0]!.stream_seq, 0);
  assert.equal(runs[0]![0]!.sample.value, 0);
  assert.equal(runs[0]![1]!.sample.value, 20);
});
test("bounded history rejects changed identity, definitions, counts, interpolation and missing extrema", () => {
  const mutations = [
    (x: ReturnType<typeof plot>) => {
      x.metadata.asset_id = "other";
    },
    (x: ReturnType<typeof plot>) => {
      x.metadata.as_of = "run:bus-001:51";
    },
    (x: ReturnType<typeof plot>) => {
      x.metadata.signals[signal].unit = "km/h";
    },
    (x: ReturnType<typeof plot>) => {
      x.metadata.timings[signal].source_id = "other";
    },
    (x: ReturnType<typeof plot>) => {
      x.metadata.quality_counts.count = 11;
    },
    (x: ReturnType<typeof plot>) => {
      x.metadata.point_count = 3;
    },
    (x: ReturnType<typeof plot>) => {
      x.metadata.interpolated = true;
    },
    (x: ReturnType<typeof plot>) => {
      x.items[1]!.sample.value = 19;
    },
    (x: ReturnType<typeof plot>) => {
      x.items[1]!.segment_id = "wrong:1:1";
    },
    (x: ReturnType<typeof plot>) => {
      x.items[1]!.stream_seq = -1;
    },
    (x: ReturnType<typeof plot>) => {
      x.items[1]!.sample.quality = "invalid";
    },
    (x: ReturnType<typeof plot>) => {
      x.items[1] = x.items[0]!;
    },
  ];
  for (const mutate of mutations) {
    const p = plot();
    mutate(p);
    assert.throws(() => parsePlot(p, query, 4));
  }
  assert.throws(() =>
    plotSegments([point(0, 0, 1), point(30, 20, 2, 2), point(60, 4, 3)]),
  );
});
test("summary checks full bucket coverage and counts without converting an empty interval to zero", () => {
  const summary = {
    metadata: {
      ...meta,
      summary_version: "history-summary/1.0.0",
      resolution: "database bucket summary",
      bucket_s: 100,
    },
    items: [
      {
        ...bucket,
        count: 0,
        good: 0,
        missing: 0,
        invalid: 0,
        continuity_breaks: 0,
        minimum: null,
        maximum: null,
        mean: null,
        first_good_s: null,
        last_good_s: null,
      },
    ],
  };
  assert.equal(parseSummary(summary, query, 100)[0]!.mean, null);
  assert.throws(() => parseSummary({ ...summary, items: [] }, query, 100));
  assert.throws(() =>
    parseSummary(
      { ...summary, items: [{ ...summary.items[0], mean: 0 }] },
      query,
      100,
    ),
  );
  assert.throws(() =>
    parseSummary(
      { ...summary, items: [{ ...summary.items[0], count: 1 }] },
      query,
      100,
    ),
  );
  const constant = {
    ...bucket,
    minimum: 1.1,
    maximum: 1.1,
    mean: 1.1000000000000003,
  };
  assert.equal(
    parseSummary({ ...summary, items: [constant] }, query, 100)[0]!.mean,
    constant.mean,
  );
});
function raw(t: number, id: number) {
  const { segment_id: _segment, ...p } = point(t, t, id);
  return {
    ...p,
    run_id: "run",
    asset_id: "bus-001",
    dashboard_revision: 40,
    manifest_sha256: "b".repeat(64),
    schema_version: "3.0.0",
    catalog_version: "1.2.0",
  };
}
test("raw continuation binds the query and last row, rejects repeated/changed pages and keeps provenance", () => {
  const first = raw(0, 1),
    last = raw(1, 2);
  const next = JSON.stringify([
    query.snap.cursor,
    "raw-export/1.0.0",
    [signal],
    0,
    100,
    2,
    1,
    last.event_id,
    signal,
  ]);
  const body = {
    metadata: {
      ...meta,
      export_version: "1.1.0",
      resolution: "raw",
      page_limit: 2,
      row_count: 2,
    },
    items: [first, last],
    next,
  };
  const parsed = parseExportPage(body, query, 2);
  assert.equal(parsed.items[0]!.stream_seq, 0);
  assert.equal(parsed.items[0]!.manifest_sha256, "b".repeat(64));
  assert.throws(() => parseExportPage(body, query, 2, last));
  assert.throws(() =>
    parseExportPage(
      { ...body, next: next.replace('"raw-export/1.0.0"', '"unknown"') },
      query,
      2,
    ),
  );
  assert.throws(() =>
    parseExportPage({ ...body, items: [last, first] }, query, 2),
  );
  assert.throws(() =>
    parseExportPage(
      { ...body, items: [first, { ...last, manifest_sha256: "bad" }] },
      query,
      2,
    ),
  );
  assert.throws(() =>
    parseExportPage(
      { ...body, items: [first, { ...last, dashboard_revision: 51 }] },
      query,
      2,
    ),
  );
});
test("CSV keeps raw numeric values and quotes metadata/newlines while neutralizing formula-like text", () => {
  const p = raw(0, 1);
  p.sample.reason = '=HYPERLINK("example")\nsecond line';
  const csv = exportCsv(meta, [p]);
  assert.ok(csv.startsWith("record_type,metadata_json,"));
  assert.ok(csv.includes('metadata,"{""mode""'));
  assert.ok(csv.includes("'="));
  assert.ok(csv.includes('""example""'));
  assert.ok(csv.includes(",good,0,,"));
  assert.ok(csv.endsWith("\r\n"));
});
