import assert from "node:assert/strict";
import { test } from "node:test";
import {
  historyBatches,
  validateSummaryBudget,
  historyJson,
  sameHistorySnapshot,
} from "../src/features/history/historyRequests.ts";
import type { HistoryQuery } from "../src/features/history/historyModel.ts";

test("every registered signal is selectable while requests retain five-signal bounds", () => {
  const signals = Array.from({ length: 37 }, (_, i) => `signal.${i}`);
  const q = {
    signals,
    bounds: { from: 5, to: 90 },
    snap: {
      cursor: "run:bus:41",
      items: signals.map((signal_id) => ({ signal_id })),
    },
  } as HistoryQuery;
  const batches = historyBatches(q);
  assert.deepEqual(
    batches.flatMap((b) => b.signals),
    signals,
  );
  assert.equal(batches.length, 8);
  for (const b of batches) {
    assert.ok(b.signals.length <= 5);
    assert.equal(b.snap, q.snap);
    assert.equal(b.bounds, q.bounds);
  }
  assert.deepEqual(historyBatches({ ...q, signals: [] }), []);
  assert.throws(() => historyBatches({ ...q, signals: ["unregistered"] }));
});
test("history reader preserves response-size limits and authentication", async () => {
  const original = globalThis.fetch;
  let auth = "";
  try {
    globalThis.fetch = async (_url, init) => {
      auth = (init?.headers as Record<string, string>).Authorization;
      return new Response('{"value":0}');
    };
    assert.deepEqual(
      (
        await historyJson(
          "/v1/plot",
          "test-credential",
          new AbortController().signal,
        )
      ).body,
      { value: 0 },
    );
    assert.equal(auth, "Bearer test-credential");
    await assert.rejects(
      historyJson(
        "/v1/plot",
        "test-credential",
        new AbortController().signal,
        3,
      ),
      /byte budget/,
    );
    globalThis.fetch = async () => new Response("{}", { status: 403 });
    await assert.rejects(
      historyJson("/v1/plot", "test-credential", new AbortController().signal),
      /unavailable/,
    );
  } finally {
    globalThis.fetch = original;
  }
});
test("a policy or snapshot change between batches cannot silently mix histories", () => {
  const first = {
    policy_sha256: "a",
    as_of: "run:bus:10",
    signals: { speed: {} },
  };
  sameHistorySnapshot(first, { ...first, signals: { charge: {} } });
  assert.throws(() =>
    sameHistorySnapshot(first, { ...first, policy_sha256: "b" }),
  );
  assert.throws(() =>
    sameHistorySnapshot(first, { ...first, as_of: "run:bus:11" }),
  );
});

test("summary budget covers the complete selection across batches", () => {
  const q = {
    signals: Array(20).fill("signal"),
    bounds: { from: 0, to: 250 },
  } as HistoryQuery;
  validateSummaryBudget(q, 1);
  assert.throws(() =>
    validateSummaryBudget({ ...q, bounds: { from: 0, to: 251 } }, 1),
  );
  for (const width of [0, -1, NaN, Infinity])
    assert.throws(() => validateSummaryBudget(q, width));
});
