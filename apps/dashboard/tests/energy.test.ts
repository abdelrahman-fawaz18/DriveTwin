import assert from "node:assert/strict";
import { test } from "node:test";
import { parseEnergy } from "../src/features/forecast/energyModel.ts";
import { checkExtensions } from "../src/features/forecast/energyExtensions.ts";

function view() {
  return {
    version: "energy-forecast-view/1.0.0",
    run_id: "run",
    asset_id: "bus-001",
    as_of: "2026-11-04T20:00:00Z",
    status: "unavailable",
    reason_codes: ["source_paused"],
    values: null,
    uncertainty: null,
    current_record_id: null,
    items: [],
    next: null,
    model_qualified: false,
    automatic_dispatch_approved: false,
  };
}
test("unavailable forecasts have no numeric current values", () => {
  assert.equal(parseEnergy(view(), "run", "bus-001").values, null);
  assert.throws(() => parseEnergy({ ...view(), values: 0 }, "run", "bus-001"));
  assert.throws(() =>
    parseEnergy(
      { ...view(), current_record_id: "a".repeat(64) },
      "run",
      "bus-001",
    ),
  );
});

test("legacy responses remain readable while invalid input age is rejected", () => {
  assert.equal(parseEnergy(view(), "run", "bus-001").extensions, undefined);
  for (const input_age_s of [-1, NaN, Infinity, "1"])
    assert.throws(() =>
      parseEnergy({ ...view(), input_age_s }, "run", "bus-001"),
    );
  assert.equal(
    parseEnergy({ ...view(), input_age_s: 0 }, "run", "bus-001").input_age_s,
    0,
  );
});

function extension() {
  return {
    version: "energy-mission-extensions/1.0.0",
    explanation: null,
    stations: {
      version: "station-assessments/1.0.0",
      profile_sha256: "a".repeat(64),
      reason_codes: [],
      items: [
        {
          station_id: "station",
          hypothetical: true,
          availability: "unverified",
          status: "at_station_approach",
          reason_codes: [],
          values: { net_mission_energy_kwh: 0 },
          uncertainty: null,
          path: {
            remaining_distance_m: 0,
            horizon_duration_s: 0,
            edges: ["road"],
            approach_lane_id: "road_0",
            motion_assumption: "declared",
            physical_site_access: "unverified",
          },
        },
      ],
    },
  };
}
test("zero-distance station is valid; duplicate stations and invalid paths are rejected", () => {
  const x = extension();
  const values = (v: unknown) => typeof v === "object" && v !== null;
  checkExtensions(x, values, () => true);
  const duplicate = structuredClone(x);
  duplicate.stations.items.push(duplicate.stations.items[0]);
  assert.throws(() => checkExtensions(duplicate, values, () => true));
  x.stations.items[0].path.remaining_distance_m = -1;
  assert.throws(() => checkExtensions(x, values, () => true));
});
test("unavailable station values and unsupported coverage claims are rejected", () => {
  const x: any = extension();
  x.stations.items[0].status = "unavailable";
  assert.throws(() =>
    checkExtensions(
      x,
      () => true,
      () => true,
    ),
  );
  x.stations.items[0].status = "reachable_under_assumptions";
  x.stations.items[0].uncertainty = {
    station_endpoint_coverage_measured: true,
  };
  assert.throws(() =>
    checkExtensions(
      x,
      () => true,
      () => true,
    ),
  );
});
test("unavailable current views cannot expose explanation or reference values", () => {
  assert.throws(() =>
    parseEnergy({ ...view(), extensions: extension() }, "run", "bus-001"),
  );
  assert.throws(() =>
    parseEnergy({ ...view(), reference_values: {} }, "run", "bus-001"),
  );
});
test("mixed asset, run, invalid date and unauthorized promotion are rejected", () => {
  for (const changed of [
    { asset_id: "bus-002" },
    { run_id: "other" },
    { as_of: "bad" },
    { model_qualified: true },
    { automatic_dispatch_approved: true },
  ])
    assert.throws(() =>
      parseEnergy({ ...view(), ...changed }, "run", "bus-001"),
    );
});
test("available forecasts require finite values, a scenario envelope and the actual record", () => {
  const values = {
    net_mission_energy_kwh: 4,
    projected_final_soc: 0.7,
    reserve_feasible: true,
    energy_plan_feasible: true,
    conditional_distance_to_reserve_m: null,
    conditional_time_to_reserve_s: null,
  };
  const uncertainty = {
    method: "training-joint-grid-envelope/1.0.0",
    minimum_energy_kwh: 2,
    maximum_energy_kwh: 6,
    minimum_final_soc: 0.68,
    maximum_final_soc: 0.72,
    probability_guarantee: false,
  };
  const payload = {
    ...view(),
    version: "persistent-energy-forecast/1.0.0",
    status: "advisory",
    record_id: "a".repeat(64),
    release_sha256: "b".repeat(64),
    model_version: "candidate",
    numerical_model_version: "numerical",
    values,
    uncertainty,
    observation_references: [],
    source_clock: { sim_time_s: 120 },
  };
  const x = {
    ...view(),
    status: "advisory",
    values,
    uncertainty,
    current_record_id: payload.record_id,
    items: [{ sequence: 1, created_at: view().as_of, payload }],
  };
  assert.equal(parseEnergy(x, "run", "bus-001").status, "advisory");
  assert.throws(() =>
    parseEnergy(
      { ...x, values: { ...values, net_mission_energy_kwh: NaN } },
      "run",
      "bus-001",
    ),
  );
  assert.throws(() =>
    parseEnergy(
      { ...x, uncertainty: { ...uncertainty, probability_guarantee: true } },
      "run",
      "bus-001",
    ),
  );
  assert.throws(() => parseEnergy({ ...x, items: [] }, "run", "bus-001"));
  assert.throws(() =>
    parseEnergy({ ...x, items: [x.items[0], x.items[0]] }, "run", "bus-001"),
  );
});
