import assert from "node:assert/strict";
import { test } from "node:test";
import { parseRouteEnergy } from "../src/features/forecast/routeEnergyModel.ts";

function view() {
  return {
    version: "route-energy-view/2.0.0",
    run_id: "run",
    asset_id: "bus",
    configured: true,
    as_of_real_life_s: 100,
    status: "unavailable",
    reason: "forecast_pending",
    current: null,
    current_fence: null,
    working_age_s: null,
    real_life_age_s: null,
    lifecycle: null,
    items: [],
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
function result() {
  const ledger: Record<string, number | boolean | string[]> = {};
  for (const key of [
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
  ])
    ledger[key] = 0;
  ledger.reserve_breached = false;
  ledger.motion_feasible = true;
  ledger.assumptions = ["declared_air_density"];
  return {
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
}

test("the charging link flag is optional and strictly boolean", () => {
  assert.equal(
    parseRouteEnergy(view(), "run", "bus").charging_advisor_configured,
    undefined,
  );
  const available = { ...view(), charging_advisor_configured: true };
  assert.equal(
    parseRouteEnergy(available, "run", "bus").charging_advisor_configured,
    true,
  );
  assert.throws(() =>
    parseRouteEnergy(
      { ...available, charging_advisor_configured: "true" },
      "run",
      "bus",
    ),
  );
});
test("infeasible diagnostics cannot bypass current source availability or record binding", () => {
  const p = {
    ...result(),
    status: "infeasible",
    reason: "unserved_energy_or_power",
  };
  p.summary.arrival_soc = null as unknown as number;
  p.summary.ledger.motion_feasible = false;
  const row = {
    fence: 1,
    created_at: "2026-11-13T00:00:00Z",
    classification: "current",
    trigger: "periodic",
    payload: p,
  };
  const response = {
    ...view(),
    reason: p.reason,
    working_age_s: 1,
    real_life_age_s: 1,
    items: [row],
    diagnostic: { fence: 1, payload: p },
  };
  assert.equal(parseRouteEnergy(response, "run", "bus").diagnostic?.fence, 1);
  assert.throws(() =>
    parseRouteEnergy(
      { ...response, reason: "source_stale_or_future" },
      "run",
      "bus",
    ),
  );
  assert.throws(() =>
    parseRouteEnergy(
      { ...response, diagnostic: { fence: 2, payload: p } },
      "run",
      "bus",
    ),
  );
  assert.throws(() =>
    parseRouteEnergy({ ...response, working_age_s: -1 }, "run", "bus"),
  );
});
test("route-aware current and unavailable responses stay distinct", () => {
  assert.equal(parseRouteEnergy(view(), "run", "bus").current, null);
  const available = {
    ...view(),
    status: "available",
    reason: null,
    current: result(),
    current_fence: 1,
    working_age_s: 1,
    real_life_age_s: 1,
  };
  assert.equal(
    parseRouteEnergy(available, "run", "bus").current?.summary?.arrival_soc,
    0.7,
  );
  assert.throws(() =>
    parseRouteEnergy({ ...view(), current: result() }, "run", "bus"),
  );
  assert.throws(() =>
    parseRouteEnergy(
      { ...available, current: { ...result(), status: "infeasible" } },
      "run",
      "bus",
    ),
  );
});
test("route-aware parser rejects foreign scope and nonfinite values", () => {
  const record = result();
  const available = {
    ...view(),
    status: "available",
    reason: null,
    current: record,
    current_fence: 1,
    working_age_s: 1,
    real_life_age_s: 1,
  };
  assert.throws(() => parseRouteEnergy(available, "run", "other"));
  assert.throws(() =>
    parseRouteEnergy(
      {
        ...available,
        current: { ...record, scope: { ...record.scope, asset_id: "other" } },
      },
      "run",
      "bus",
    ),
  );
  assert.throws(() =>
    parseRouteEnergy(
      {
        ...available,
        current: { ...record, summary: { ...record.summary, arrival_soc: 2 } },
      },
      "run",
      "bus",
    ),
  );
  record.summary.ledger.net_battery_j = NaN;
  assert.throws(() => parseRouteEnergy(available, "run", "bus"));
});
test("history retains unavailable results without presenting an arrival", () => {
  const historical = {
    ...result(),
    status: "unavailable",
    summary: null,
    reason: "missing_route_elevation",
  };
  const data = {
    ...view(),
    items: [
      {
        fence: 1,
        created_at: "2026-11-12T01:00:00Z",
        payload: historical,
        classification: "historical",
        trigger: "route_change",
      },
    ],
  };
  assert.equal(parseRouteEnergy(data, "run", "bus").items.length, 1);
  assert.throws(() =>
    parseRouteEnergy(
      { ...data, automatic_dispatch_approved: true },
      "run",
      "bus",
    ),
  );
});
test("saved forecasts retain their individual reserves and accept older responses", () => {
  const record = {
    fence: 1,
    created_at: "2026-11-12T01:00:00Z",
    payload: result(),
    classification: "historical",
    trigger: "route_change",
  };
  const data = {
    ...view(),
    items: [
      { ...record, fence: 3, reserve_soc: 0.1 },
      { ...record, fence: 2, reserve_soc: 0.15 },
      record,
    ],
  };
  assert.deepEqual(
    parseRouteEnergy(data, "run", "bus").items.map((item) => item.reserve_soc),
    [0.1, 0.15, undefined],
  );
  for (const reserve_soc of [-0.1, 1.1, "0.1", NaN]) {
    assert.throws(() =>
      parseRouteEnergy(
        { ...data, items: [{ ...record, reserve_soc }] },
        "run",
        "bus",
      ),
    );
  }
});
