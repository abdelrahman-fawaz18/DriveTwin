import { test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID, createHash } from "node:crypto";
import {
  verifySession,
  project,
  history,
  fixes,
  step,
  advance,
} from "../src/features/replay/replayModel.ts";
const hash = (raw: Buffer) => createHash("sha256").update(raw).digest("hex");
const artifact = (obj: unknown) => {
  const raw = Buffer.from(JSON.stringify(obj));
  return { sha256: hash(raw), raw_base64: raw.toString("base64") };
};
function fixture() {
  const run = randomUUID(),
    session = randomUUID(),
    replay = randomUUID();
  const recipe = artifact({
    manifest_version: "source-run/2.0.0",
    mode: "simulation",
    identity: {
      run_id: run,
      asset_id: "bus-001",
      source_id: "s",
      source_epoch: 1,
      session_id: session,
    },
    schema_version: "3.0.0",
    catalog_version: "1.2.0",
  });
  const definition = {
    unit: "m/s",
    source_id: "s",
    component_id: "motor",
    physical_location: "assumed",
    provenance: "simulated-observation",
    sample_period_s: 1,
    freshness_s: 5,
  };
  const packet = (
    t: number,
    value: number | null,
    quality: string,
    revision: number,
  ) => {
    const b = {
      run_id: run,
      asset_id: "bus-001",
      mode: "simulation",
      event_id: randomUUID(),
      session_id: session,
      source_id: "s",
      source_epoch: 1,
      stream_seq: revision,
      sim_time_s: t,
      observed_at: "2026-11-03T00:00:00Z",
      schema_version: "3.0.0",
      catalog_version: "1.2.0",
      manifest_sha256: recipe.sha256,
      samples: [
        {
          signal_id: "motion.speed",
          unit: "m/s",
          value,
          quality,
          provenance: "simulated-observation",
          ...(quality === "invalid"
            ? { raw_value: 1000, reason: "out_of_range" }
            : {}),
        },
      ],
    };
    const a = artifact(b);
    return {
      wire_sha256: a.sha256,
      raw_base64: a.raw_base64,
      received_at: b.observed_at,
      persisted_at: b.observed_at,
      source_revision: revision,
      seed: t < 2,
    };
  };
  const p = {
    package_version: "1.0.0",
    mode: "replay",
    replay_run_id: replay,
    namespace: "replay/" + replay,
    source_run_id: run,
    source_mode: "simulation",
    asset_id: "bus-001",
    source_cursor: `${run}:bus-001:5`,
    created_at: "2026-11-03T00:00:00Z",
    from_s: 2,
    to_s: 15,
    time_basis: "retrospective recorded simulation time",
    policy_sha256: "a".repeat(64),
    catalog: {
      catalog_version: "1.2.0",
      source_catalog_sha256: "b".repeat(64),
      signals: { "motion.speed": definition },
    },
    recipes: [recipe],
    map_context: null,
    packets: [
      packet(1, 5, "good", 1),
      packet(3, 0, "good", 2),
      packet(4, null, "missing", 3),
      packet(5, null, "invalid", 4),
      packet(12, 7, "good", 5),
    ],
    capabilities: {
      dispatch: false,
      publish_observations: false,
      write_source_state: false,
    },
  };
  return p;
}
async function load(p = fixture()) {
  const raw = Buffer.from(JSON.stringify(p));
  return verifySession(raw, hash(raw), {
    asset: p.asset_id,
    run: p.source_run_id,
    from: p.from_s,
    to: p.to_s,
  });
}
test("verified frozen replay retains before-window seed, zero, quality/raw audit and original bytes", async () => {
  const p = fixture(),
    s = await load(p);
  assert.equal(project(s, 2)[0]!.entry!.sample.value, 5);
  assert.equal(project(s, 2)[0]!.age, 1);
  assert.equal(project(s, 3)[0]!.entry!.sample.value, 0);
  assert.equal(project(s, 4)[0]!.quality, "missing");
  assert.equal(project(s, 5)[0]!.entry!.sample.raw_value, 1000);
  assert.equal(project(s, 11)[0]!.recency, "stale in replay");
  assert.equal(project(s, 12)[0]!.entry!.sample.value, 7);
  assert.deepEqual(
    history(s, "motion.speed", 4).map((x) => x.sim_time_s),
    [3, 4],
  );
  assert.deepEqual(history(s, "motion.speed", 2), []);
  assert.deepEqual(fixes(s, 15), []);
  assert.ok(Object.isFrozen(s.packets[0]!.body.samples));
  assert.equal(s.packets[0]!.raw_base64, p.packets[0]!.raw_base64);
});
test("replay is deterministic under seek and retrospective ordering, with no future value", async () => {
  const p = fixture();
  p.packets.reverse();
  const s = await load(p);
  assert.equal(project(s, 12)[0]!.entry!.sample.value, 7);
  const a = JSON.stringify(project(s, 3));
  project(s, 15);
  assert.equal(JSON.stringify(project(s, 3)), a);
  assert.equal(step(s, 3, 1), 4);
  assert.equal(step(s, 3, -1), 2);
  assert.equal(step(s, 12, 1), 15);
  assert.equal(history(s, "motion.speed", 3).length, 1);
});
test("hashes, source identity, permissions, recipe and catalog mismatches fail closed", async () => {
  const p = fixture(),
    raw = Buffer.from(JSON.stringify(p));
  await assert.rejects(
    verifySession(raw, "0".repeat(64), {
      asset: p.asset_id,
      run: p.source_run_id,
      from: 2,
      to: 15,
    }),
  );
  for (const change of [
    (x: ReturnType<typeof fixture>) => (x.namespace = "live"),
    (x: ReturnType<typeof fixture>) => (x.capabilities.dispatch = true),
    (x: ReturnType<typeof fixture>) => (x.packets[0]!.raw_base64 = "e30="),
    (x: ReturnType<typeof fixture>) => (x.packets[0]!.source_revision = 6),
    (x: ReturnType<typeof fixture>) =>
      (x.catalog.signals["motion.speed"].unit = "km/h"),
  ]) {
    const copy = structuredClone(p);
    change(copy);
    await assert.rejects(load(copy));
  }
  await assert.rejects(
    verifySession(raw, hash(raw), {
      asset: "bus-002",
      run: p.source_run_id,
      from: 2,
      to: 15,
    }),
  );
});
test("elapsed playback supports rate, clamps at end and pauses after scheduling interruption", () => {
  assert.deepEqual(advance(3, 500, 2, 15), { time: 4, interrupted: false });
  assert.equal(advance(14.9, 500, 8, 15).time, 15);
  assert.deepEqual(advance(3, 1001, 1, 15), { time: 3, interrupted: true });
  assert.deepEqual(advance(3, -1, 1, 15), { time: 3, interrupted: true });
});
