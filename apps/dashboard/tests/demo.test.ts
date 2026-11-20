import assert from "node:assert/strict";
import { test } from "node:test";
import {
  csvCell,
  createProjector,
  initialPlayer,
  parseManifest,
  projectCase,
  recordedActivity,
  readCase,
  transition,
  validateCase,
} from "../src/features/demo/demoModel.ts";
import { fixture, fixtureFiles } from "./demoFixture.ts";
import {
  recordedSnapshot,
  recordedForecasts,
  historyPoints,
} from "../src/features/demo/demoAdapter.ts";

test("the introduction pauses exactly at 30 with no default decision", () => {
  const data = fixture();
  let p = transition(initialPlayer(), { type: "start" }, data);
  for (let i = 0; i < 299; i++)
    p = transition(p, { type: "tick", delta: 0.1 }, data);
  p = transition(p, { type: "tick", delta: 0.9 }, data);
  assert.equal(p.t, 30);
  assert.equal(p.phase, "decision");
  assert.equal(p.playing, false);
  for (let i = 0; i < 100; i++)
    p = transition(p, { type: "tick", delta: 1 }, data);
  assert.equal(p.t, 30);
  assert.equal(p.choice, null);
});
test("pause, hidden tabs, long interruptions, seeking, and speed cannot skip the introduction", () => {
  const data = fixture(),
    p = transition(initialPlayer(), { type: "start" }, data);
  for (const a of [
    { type: "tick", delta: 60 },
    { type: "tick", delta: 0.5, hidden: true },
    { type: "seek", t: 30 },
    { type: "speed", speed: 20 },
    { type: "tick", delta: NaN },
  ] as const)
    assert.deepEqual(transition(p, a, data), p);
  const paused = transition(p, { type: "toggle" }, data);
  assert.deepEqual(
    transition(paused, { type: "tick", delta: 1 }, data),
    paused,
  );
});
test("both choices, all speeds, backward seek, endings, other outcome, and restart", () => {
  const data = fixture();
  for (const choice of ["charge", "continue"] as const) {
    let p = { ...initialPlayer(), phase: "decision" as const, t: 30 };
    let chosen = transition(p, { type: "choose", choice }, data);
    assert.equal(chosen.choice, choice);
    assert.deepEqual(
      transition(
        chosen,
        { type: "choose", choice: choice === "charge" ? "continue" : "charge" },
        data,
      ),
      chosen,
    );
    for (const speed of [0.5, 1, 2, 5, 10, 20]) {
      const q = transition(chosen, { type: "speed", speed }, data);
      assert.equal(
        transition(q, { type: "tick", delta: 0.5 }, data).t,
        30 + 0.5 * speed,
      );
    }
    chosen = transition(chosen, { type: "seek", t: 9999 }, data);
    assert.equal(chosen.phase, "finished");
    assert.equal(chosen.t, 60);
    const back = transition(chosen, { type: "seek", t: 10 }, data);
    assert.equal(back.choice, choice);
    assert.equal(projectCase(data, back).events.length, 0);
    assert.equal(projectCase(data, back).forecasts.length, 1);
    const other = transition(chosen, { type: "other" }, data);
    assert.equal(other.phase, "decision");
    assert.equal(other.choice, null);
    assert.equal(projectCase(data, other).transfer, undefined);
    assert.equal(projectCase(data, other).forecasts.length, 1);
    assert.equal(transition(other, { type: "restart" }, data).t, 0);
    p = { ...p, t: 30 };
  }
});
test("causal projection never exposes future forecasts, events, or transfer from either branch", () => {
  const d = fixture(),
    p = {
      ...initialPlayer(),
      phase: "outcome" as const,
      choice: "charge" as const,
      t: 36,
    };
  assert.equal(projectCase(d, p).forecasts.length, 1);
  assert.equal(projectCase(d, { ...p, t: 37 }).forecasts.length, 2);
  assert.equal(projectCase(d, p).transfer, undefined);
  assert.equal(projectCase(d, { ...p, t: 45 }).transfer?.deliveredKwh, 1);
  assert.equal(
    projectCase(d, { ...p, choice: "continue", t: 45 }).transfer,
    undefined,
  );
});
test("package validator rejects malformed identities, corrupt joins, invalid values, and premature effects", () => {
  const d = fixture();
  fixtureFiles(d);
  const assets = {
    catalog: d.catalog,
    map: d.map,
    common: d.common,
    charge: d.charge,
    continue: d.continue,
  };
  assert.doesNotThrow(() => validateCase(parseManifest(d.manifest), assets));
  for (const change of [
    (m: typeof d.manifest) => {
      m.version = "bad" as typeof m.version;
    },
    (m: typeof d.manifest) => {
      m.files.map.path = "../private.json";
    },
    (m: typeof d.manifest) => {
      m.files.map.bytes = 999999999;
    },
    (m: typeof d.manifest) => {
      m.decision.stationId = "missing";
    },
  ]) {
    const bad = structuredClone(d.manifest);
    change(bad);
    assert.throws(() => validateCase(parseManifest(bad), assets));
  }
  const bad = structuredClone(assets);
  bad.charge.frames[0]![0] = 30;
  assert.throws(() => validateCase(d.manifest, bad));
  const future = structuredClone(assets);
  future.common.forecasts[0]!.available = 31;
  assert.throws(() => validateCase(d.manifest, future));
  const invalid = structuredClone(assets);
  invalid.common.frames[0]![1][0] = NaN;
  assert.throws(() => validateCase(d.manifest, invalid));
  const effects = structuredClone(assets);
  effects.charge.events[0]!.t = 7;
  assert.throws(() => validateCase(d.manifest, effects));
});
test("static loader verifies every file, omits credentials, and rejects missing or corrupt data", async () => {
  const files = fixtureFiles();
  const original = globalThis.fetch;
  let corrupt = false,
    missing = false;
  globalThis.fetch = async (url, options) => {
    assert.equal(options?.credentials, "omit");
    assert.equal(options?.redirect, "error");
    const name = new URL(String(url)).pathname.split("/").at(-1)!;
    if (missing && name === "map.json")
      return new Response(null, { status: 404 });
    const bytes =
      corrupt && name === "charge.json" ? Buffer.from("{}") : files[name]!;
    return new Response(bytes);
  };
  try {
    const d = await readCase(
      new URL("https://example.test/project/cases/montreal/manifest.json"),
    );
    assert.equal(d.catalog.length, 20);
    corrupt = true;
    await assert.rejects(() =>
      readCase(new URL("https://example.test/manifest.json")),
    );
    corrupt = false;
    missing = true;
    await assert.rejects(() =>
      readCase(new URL("https://example.test/manifest.json")),
    );
  } finally {
    globalThis.fetch = original;
  }
});
test("CSV export quotes text and neutralizes formulas while retaining signed numeric values", () => {
  assert.equal(csvCell('reading, "original"'), '"reading, ""original"""');
  assert.equal(csvCell("=SUM(A1)"), '"\'=SUM(A1)"');
  assert.equal(csvCell(-1.25), "-1.25");
  assert.equal(csvCell(null), '""');
});

test("recorded adapters retain raw units and quality, cutoff provenance, and causal comparison timing", () => {
  const d = fixture(),
    p = { ...initialPlayer(), t: 30 };
  d.common.frames[29]![1][2] = 0;
  const snap = recordedSnapshot(d, p);
  assert.equal(snap.items[2]!.observation!.sample.value, 0);
  assert.equal(snap.items[2]!.definition.unit, "m/s");
  assert.equal(snap.items[2]!.source_state, "recorded");
  const records = recordedForecasts(d, p);
  assert.deepEqual(
    records[0]!.payload.summary!.ledger,
    d.common.forecasts[0]!.summary!.ledger,
  );
  assert.equal(records[0]!.payload.cutoff_working_s, 1);
  assert.equal(
    historyPoints(d, projectCase(d, p).frames, "motion.speed", 0, 30).at(-1)!
      .value,
    0,
  );
  d.charge.comparisons = [
    { available: 45, from: 31, until: 40, predicted: 8, observed: 7, error: 1 },
  ];
  assert.equal(
    projectCase(d, { ...p, choice: "charge", t: 44 }).comparisons.length,
    0,
  );
  assert.equal(
    projectCase(d, { ...p, choice: "charge", t: 45 }).comparisons[0]!.error,
    1,
  );
  assert.equal(
    projectCase(d, { ...p, choice: "continue", t: 45 }).comparisons.length,
    0,
  );
});

test("new packages require original finite ledgers and exit clears the selected outcome", () => {
  const d = fixture();
  fixtureFiles(d);
  const assets = {
    catalog: d.catalog,
    map: d.map,
    common: d.common,
    charge: d.charge,
    continue: d.continue,
  };
  const bad = structuredClone(assets);
  delete bad.common.forecasts[0]!.summary!.ledger;
  assert.throws(
    () => validateCase(d.manifest, bad),
    /Missing recorded energy ledger/,
  );
  const mismatch = structuredClone(assets);
  mismatch.common.forecasts[0]!.summary!.ledger!.net_battery_j = 1;
  assert.throws(
    () => validateCase(d.manifest, mismatch),
    /differs from summary/,
  );
  const exited = transition(
    { ...initialPlayer(), phase: "outcome", choice: "charge", t: 55 },
    { type: "exit" },
    d,
  );
  assert.equal(exited.choice, null);
  assert.equal(exited.t, 0);
  assert.equal(exited.phase, "ready");
  assert.equal(projectCase(d, exited).frames.length, 0);
  const old = structuredClone(d.manifest);
  old.version = "branched-trip-demo/1.0.0";
  old.decision.at = 20;
  assert.doesNotThrow(() => parseManifest(old));
  old.decision.at = 30;
  assert.throws(() => parseManifest(old));
});

test("projection reuse retains inclusive causal boundaries, backward seeking, and branch isolation", () => {
  const d = fixture();
  d.charge.comparisons = [
    {
      available: 45.25,
      from: 31,
      until: 40,
      predicted: 8,
      observed: 7,
      error: 1,
    },
  ];
  const project = createProjector(d);
  const player = {
    ...initialPlayer(),
    phase: "outcome" as const,
    choice: "charge" as const,
  };
  for (const choice of [null, "charge", "continue", "charge"] as const)
    for (const t of [0, 1, 1.1, 30, 36, 37, 45, 45.249, 45.25, 60, 10, 0])
      assert.deepEqual(
        project({ ...player, choice, t }),
        projectCase(d, { ...player, choice, t }),
      );
  const before = project({ ...player, t: 45.1 });
  assert.equal(project({ ...player, t: 45.2 }), before);
  assert.notEqual(project({ ...player, t: 45.25 }), before);
  const view = project({ ...player, t: 45.5 });
  assert.deepEqual(
    recordedForecasts(d, player, view),
    recordedForecasts(d, { ...player, t: 45.5 }),
  );
  const snapshot = recordedSnapshot(d, { ...player, t: 45.5 }, view);
  assert.equal(snapshot.items[0]!.age_s, 0.5);
});

test("Activity identifies simultaneous records separately through filtering, seek, and branch reset", () => {
  const events = [
    { t: 37, kind: "approval", title: "First approval" },
    { t: 37, kind: "approval", title: "Second approval" },
    { t: 37, kind: "stop_inserted", title: "Observed stop" },
  ];
  assert.equal(
    recordedActivity(events, "all", 1).selected?.event.title,
    "Second approval",
  );
  assert.equal(recordedActivity(events, "approval", 1).selected?.id, 1);
  assert.equal(recordedActivity(events, "stop_inserted", 1).selected?.id, 2);
  assert.equal(recordedActivity(events, "missing", 1).selected, undefined);
  assert.equal(recordedActivity([], "all", 1).selected, undefined);
  assert.equal(recordedActivity(events, "all", null).selected?.id, 2);
  const d = fixture();
  d.charge.events = events;
  const chosen = {
    ...initialPlayer(),
    phase: "outcome" as const,
    choice: "charge" as const,
    t: 37,
  };
  const back = transition(chosen, { type: "seek", t: 36 }, d);
  assert.equal(
    recordedActivity(projectCase(d, back).events, "all", 1).selected,
    undefined,
  );
  const reset = transition(chosen, { type: "other" }, d);
  assert.equal(reset.generation, chosen.generation + 1);
  assert.equal(
    recordedActivity(projectCase(d, reset).events, "all", null).selected,
    undefined,
  );
});
