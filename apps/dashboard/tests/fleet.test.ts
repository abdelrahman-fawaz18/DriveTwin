import assert from "node:assert/strict";
import { test } from "node:test";
import { startFleetPolling } from "../src/features/fleet/fleetPolling.ts";
import type { FleetAsset } from "../src/features/fleet/fleetPolling.ts";

function snapshot(asset: string, revision = 1, run = "run") {
  return {
    version: "1.0.0",
    mode: "simulation",
    asset_id: asset,
    run_id: run,
    as_of: "2026-11-04T00:00:00Z",
    cursor: `${run}:${asset}:${revision}`,
    items: [],
  };
}
function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}
async function until(condition: () => boolean) {
  const end = performance.now() + 2000;
  while (!condition()) {
    assert.ok(performance.now() < end, "condition timed out");
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

test("fleet polling is bounded to two requests and keeps asset scope and credentials in headers", async () => {
  let count = 0,
    maximum = 0,
    accepted = 0;
  const pending: (() => void)[] = [];
  const seen: Record<string, FleetAsset> = {};
  const stop = startFleetPolling(
    ["a", "b", "c", "d"],
    "private-test-reader",
    (asset, state) => {
      seen[asset] = state;
      if (state.connection === "live") accepted++;
    },
    () => assert.fail("unexpected auth failure"),
    {
      intervalMs: 1000,
      fetcher: async (input, init) => {
        assert.equal(init?.cache, "no-store");
        assert.deepEqual(init?.headers, {
          Authorization: "Bearer private-test-reader",
        });
        assert.ok(!String(input).includes("private-test-reader"));
        const asset = new URL(
          String(input),
          "http://localhost",
        ).searchParams.get("asset_id")!;
        count++;
        maximum = Math.max(maximum, count);
        await new Promise<void>((resolve) => pending.push(resolve));
        count--;
        return response(snapshot(asset));
      },
    },
  );
  try {
    await until(() => pending.length === 2);
    pending.shift()!();
    pending.shift()!();
    await until(() => pending.length === 2 && accepted === 2);
    pending.shift()!();
    pending.shift()!();
    await until(() => accepted === 4);
    assert.equal(maximum, 2);
    for (const asset of ["a", "b", "c", "d"])
      assert.equal(seen[asset]?.snap?.asset_id, asset);
  } finally {
    stop();
  }
});

test("fleet rejects wrong asset, recording and backward revision without replacing the last good reading", async () => {
  let n = 0;
  const states: FleetAsset[] = [];
  const stop = startFleetPolling(
    ["a"],
    "reader",
    (_, state) => states.push(state),
    () => assert.fail(),
    {
      intervalMs: 5,
      fetcher: async () => {
        const cases = [
          snapshot("a", 5),
          snapshot("b", 6),
          snapshot("a", 6, "other"),
          snapshot("a", 4),
          snapshot("a", 6),
        ];
        return response(cases[Math.min(n++, cases.length - 1)]);
      },
    },
  );
  try {
    await until(() => states.some((s) => s.snap?.cursor === "run:a:6"));
    const failures = states.filter((s) => s.connection === "disconnected");
    assert.equal(failures.length, 3);
    assert.ok(failures.every((s) => s.snap?.cursor === "run:a:5"));
    assert.ok(states.every((s) => !s.snap || s.snap.asset_id === "a"));
  } finally {
    stop();
  }
});

test("authorization failure stops the entire fleet and suppresses a late response", async () => {
  let blocked = 0;
  const updates: FleetAsset[] = [];
  let release!: () => void;
  const stop = startFleetPolling(
    ["a", "b", "c"],
    "reader",
    (_, state) => updates.push(state),
    () => blocked++,
    {
      fetcher: async (url) => {
        if (String(url).endsWith("a")) return response({}, 403);
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return response(snapshot("b"));
      },
    },
  );
  try {
    await until(() => blocked === 1);
    const before = updates.length;
    release();
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.equal(updates.length, before);
    assert.ok(updates.every((s) => s.snap === null));
  } finally {
    stop();
  }
});

test("stopped or hung reads never retain live status indefinitely or update an unmounted view", async () => {
  let requests = 0;
  const states: FleetAsset[] = [];
  const stop = startFleetPolling(
    ["a"],
    "reader",
    (_, state) => states.push(state),
    () => assert.fail(),
    {
      intervalMs: 5,
      timeoutMs: 1000,
      staleMs: 25,
      fetcher: async (_, init) => {
        if (!requests++) return response(snapshot("a"));
        await new Promise<void>((_, reject) =>
          init?.signal?.addEventListener("abort", () =>
            reject(Error("aborted")),
          ),
        );
        return response(snapshot("a", 2));
      },
    },
  );
  await until(() => states.some((s) => s.connection === "live"));
  await until(() => states.at(-1)?.connection === "disconnected");
  assert.equal(states.at(-1)?.snap?.cursor, "run:a:1");
  stop();
  const before = states.length;
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(states.length, before);
});
