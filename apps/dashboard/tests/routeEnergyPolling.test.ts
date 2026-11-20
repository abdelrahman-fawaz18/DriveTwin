import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import { parseRouteEnergy } from "../src/features/forecast/routeEnergyModel.ts";
import type { useRouteEnergy } from "../src/features/forecast/useRouteEnergy.ts";

function available() {
  const ledger: Record<string, unknown> = Object.fromEntries(
    [
      "initial_energy_j",
      "final_energy_j",
      "minimum_energy_j",
      "capacity_j",
      "battery_draw_j",
      "battery_recovery_j",
      "net_battery_j",
      "acceleration_work_j",
      "grade_work_j",
      "rolling_work_j",
      "air_work_j",
      "propulsion_required_j",
      "auxiliary_required_j",
      "regeneration_potential_j",
      "regeneration_used_j",
      "rejected_regeneration_j",
      "unserved_energy_j",
      "drive_conversion_loss_j",
      "braking_to_heat_j",
      "energy_balance_error_j",
    ].map((name) => [name, 0]),
  );
  Object.assign(ledger, {
    capacity_j: 1_260_000_000,
    initial_energy_j: 1_008_000_000,
    motion_feasible: true,
    reserve_breached: false,
    assumptions: [],
  });
  const current = {
    version: "route-aware-result/2.0.0",
    model_version: "route-aware-energy/2.0.0-dev",
    input_sha256: "a".repeat(64),
    route_sha256: "b".repeat(64),
    vehicle_sha256: "c".repeat(64),
    scope: {
      run_id: "run",
      asset_id: "bus",
      source_id: "adapter",
      source_epoch: 1,
      session_id: "session",
    },
    generation: 1,
    cutoff_working_s: 30,
    requested_real_life_s: 99,
    status: "available",
    reason: null,
    uncertainty_method: "not_established",
    summary: {
      remaining_distance_m: 1000,
      duration_working_s: 200,
      arrival_working_s: 230,
      arrival_soc: 0.7,
      minimum_soc: 0.69,
      ledger,
    },
  };
  return {
    version: "route-energy-view/2.0.0",
    run_id: "run",
    asset_id: "bus",
    configured: true,
    as_of_real_life_s: 100,
    status: "available",
    reason: null,
    current,
    current_fence: 1,
    working_age_s: 1,
    real_life_age_s: 1,
    lifecycle: null,
    items: [
      {
        fence: 1,
        created_at: "2026-11-18T00:00:00Z",
        classification: "current",
        trigger: "periodic",
        payload: current,
      },
    ],
    comparisons: [],
    comparison_status: {
      status: "insufficient_data",
      intervals: 0,
      threshold_j: 900000,
    },
    next: null,
    automatic_dispatch_approved: false,
  };
}

function polling(statuses: number[]) {
  const states: unknown[] = [];
  let cursor = 0,
    initialized = false,
    serial = 0;
  let effect: () => () => void;
  const timers = new Map<number, { run: () => Promise<void>; ms: number }>();
  const module = { exports: {} as { useRouteEnergy: typeof useRouteEnergy } };
  // Execute the production hook with deterministic state, fetch and clocks.
  // No DOM, wall-clock delay, network request, or timer escapes this context.
  const code = ts.transpileModule(
    readFileSync(
      new URL("../src/features/forecast/useRouteEnergy.ts", import.meta.url),
      "utf8",
    ),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
      },
    },
  ).outputText;
  runInNewContext(code, {
    exports: module.exports,
    require: (name: string) => {
      if (name === "./routeEnergyModel.ts") return { parseRouteEnergy };
      assert.equal(name, "react");
      return {
        useEffect: (run: typeof effect) => {
          if (!initialized) effect = run;
        },
        useState: (initial: unknown) => {
          const index = cursor++;
          if (!(index in states)) states[index] = initial;
          return [
            states[index],
            (next: unknown) => {
              states[index] = next;
            },
          ];
        },
      };
    },
    performance: { now: () => 1000 },
    AbortController,
    fetch: async () => {
      assert.ok(statuses.length, "Unexpected extra request");
      const status = statuses.shift()!;
      return { status, ok: status === 200, json: async () => available() };
    },
    setInterval: () => 0,
    clearInterval: () => undefined,
    setTimeout: (run: () => Promise<void>, ms: number) => {
      const id = ++serial;
      timers.set(id, { run, ms });
      return id;
    },
    clearTimeout: (id: number) => timers.delete(id),
  });
  const unauthorized = () => assert.fail("Unexpected unauthorized response");
  const render = () => {
    cursor = 0;
    return module.exports.useRouteEnergy(
      "reader",
      "run",
      "bus",
      true,
      unauthorized,
    );
  };
  render();
  initialized = true;
  const stop = effect!();
  return {
    stop,
    render,
    async settle() {
      for (let i = 0; i < 12; i++) await Promise.resolve();
    },
    async next() {
      const [id, timer] = [...timers].find(([, t]) => t.ms === 1500)!;
      timers.delete(id);
      await timer.run();
    },
  };
}

test("route-aware polling recovers after a transient unsupported response", async () => {
  const hook = polling([404, 200]);
  try {
    await hook.settle();
    assert.equal(hook.render().unsupported, true);
    await hook.next();
    const recovered = hook.render();
    assert.equal(recovered.unsupported, false);
    assert.equal(recovered.fresh, true);
    assert.equal(recovered.current?.summary?.arrival_soc, 0.7);
  } finally {
    hook.stop();
  }
});

test("an unsupported response hides current values and retains saved history", async () => {
  const hook = polling([200, 404, 200]);
  try {
    await hook.settle();
    assert.equal(hook.render().current?.summary?.arrival_soc, 0.7);
    await hook.next();
    const unsupported = hook.render();
    assert.equal(unsupported.unsupported, true);
    assert.equal(unsupported.fresh, false);
    assert.equal(unsupported.current, null);
    assert.equal(unsupported.diagnostic, null);
    assert.equal(unsupported.data?.items.length, 1);
    await hook.next();
    assert.equal(hook.render().unsupported, false);
    assert.equal(hook.render().current?.summary?.arrival_soc, 0.7);
  } finally {
    hook.stop();
  }
});
