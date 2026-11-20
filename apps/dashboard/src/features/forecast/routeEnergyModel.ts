export interface RouteSummary {
  remaining_distance_m: number;
  duration_working_s: number;
  arrival_working_s: number;
  arrival_soc: number | null;
  minimum_soc: number;
  ledger: Record<string, number | boolean | string[]> & {
    initial_energy_j: number;
    capacity_j: number;
    net_battery_j: number;
    battery_draw_j: number;
    battery_recovery_j: number;
    auxiliary_required_j: number;
    propulsion_required_j: number;
    regeneration_used_j: number;
    unserved_energy_j: number;
    rejected_regeneration_j: number;
    reserve_breached: boolean;
    motion_feasible: boolean;
    assumptions: string[];
  };
}
export interface RouteForecast {
  version: "route-aware-result/2.0.0";
  model_version: string;
  input_sha256: string;
  route_sha256: string;
  scope: {
    run_id: string;
    asset_id: string;
    source_id: string;
    source_epoch: number;
    session_id: string;
  };
  generation: number;
  cutoff_working_s: number;
  requested_real_life_s: number;
  status: "available" | "infeasible" | "unavailable";
  reason: string | null;
  uncertainty_method: "not_established";
  summary: RouteSummary | null;
}
export interface RouteEnergyView {
  version: "route-energy-view/2.0.0";
  run_id: string;
  asset_id: string;
  configured: boolean;
  as_of_real_life_s: number;
  status: "available" | "unavailable";
  reason: string | null;
  current: RouteForecast | null;
  current_fence: number | null;
  diagnostic?: { fence: number; payload: RouteForecast } | null;
  working_age_s: number | null;
  real_life_age_s: number | null;
  lifecycle: {
    phase: string;
    reason: string;
    generation: number;
    updated_at: string;
  } | null;
  items: {
    fence: number;
    created_at: string;
    payload: RouteForecast;
    classification: string;
    trigger: string;
    route_revision?: string;
    reserve_soc?: number | null;
  }[];
  next: number | null;
  comparison_status: {
    status:
      | "insufficient_data"
      | "persistent_mismatch"
      | "no_persistent_mismatch";
    intervals: number;
    threshold_j: number;
  };
  comparisons: {
    fence: number;
    created_at: string;
    payload: {
      scope: RouteForecast["scope"];
      input_sha256: string;
      from_working_s: number;
      until_working_s: number;
      predicted_net_j: number;
      observed_net_j: number;
      signed_error_j: number;
    };
  }[];
  automatic_dispatch_approved: false;
  charging_advisor_configured?: true;
  charging_startup_waiting?: true;
}
const object = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const finite = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v);
const count = (v: unknown): v is number =>
  finite(v) && Number.isSafeInteger(v) && v >= 1;
const hash = (v: unknown) => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const fraction = (v: unknown) => finite(v) && v >= -1e-12 && v <= 1 + 1e-12;
const words = (v: unknown) =>
  typeof v === "string" && v.length > 0 && v.length <= 256;
function forecast(v: unknown, run: string, asset: string): boolean {
  if (
    !object(v) ||
    v.version !== "route-aware-result/2.0.0" ||
    !words(v.model_version) ||
    !hash(v.input_sha256) ||
    !hash(v.route_sha256) ||
    !hash(v.vehicle_sha256) ||
    !object(v.scope) ||
    v.scope.run_id !== run ||
    v.scope.asset_id !== asset ||
    !words(v.scope.source_id) ||
    !count(v.scope.source_epoch) ||
    !words(v.scope.session_id) ||
    !count(v.generation) ||
    !finite(v.cutoff_working_s) ||
    v.cutoff_working_s < 0 ||
    !finite(v.requested_real_life_s) ||
    v.requested_real_life_s < 0 ||
    !["available", "infeasible", "unavailable"].includes(String(v.status)) ||
    v.uncertainty_method !== "not_established"
  )
    return false;
  if (v.status === "unavailable") return v.summary === null && words(v.reason);
  const s = v.summary;
  if (
    !object(s) ||
    ![s.remaining_distance_m, s.duration_working_s, s.arrival_working_s].every(
      (n) => finite(n) && n >= 0,
    ) ||
    !fraction(s.minimum_soc) ||
    !object(s.ledger)
  )
    return false;
  const ledger = s.ledger;
  const energy = [
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
  ];
  if (
    !energy.every((k) => finite(ledger[k])) ||
    typeof ledger.reserve_breached !== "boolean" ||
    typeof ledger.motion_feasible !== "boolean" ||
    !Array.isArray(ledger.assumptions) ||
    ledger.assumptions.length > 128 ||
    !ledger.assumptions.every(words)
  )
    return false;
  if (v.status === "available")
    return (
      fraction(s.arrival_soc) &&
      ledger.motion_feasible === true &&
      v.reason === null
    );
  return (
    s.arrival_soc === null &&
    ledger.motion_feasible === false &&
    v.reason === "unserved_energy_or_power"
  );
}
export function parseRouteEnergy(
  v: unknown,
  run: string,
  asset: string,
): RouteEnergyView {
  if (
    !object(v) ||
    v.version !== "route-energy-view/2.0.0" ||
    v.run_id !== run ||
    v.asset_id !== asset ||
    typeof v.configured !== "boolean" ||
    !finite(v.as_of_real_life_s) ||
    v.as_of_real_life_s < 0 ||
    !["available", "unavailable"].includes(String(v.status)) ||
    v.automatic_dispatch_approved !== false ||
    (v.charging_advisor_configured !== undefined &&
      v.charging_advisor_configured !== true) ||
    (v.charging_startup_waiting !== undefined &&
      v.charging_startup_waiting !== true) ||
    ![v.working_age_s, v.real_life_age_s].every(
      (n) => n === null || finite(n),
    ) ||
    !Array.isArray(v.items) ||
    !Array.isArray(v.comparisons) ||
    v.comparisons.length > 20 ||
    v.items.length > 50 ||
    !(v.next === null || count(v.next)) ||
    !object(v.comparison_status) ||
    ![
      "insufficient_data",
      "persistent_mismatch",
      "no_persistent_mismatch",
    ].includes(String(v.comparison_status.status)) ||
    !finite(v.comparison_status.threshold_j) ||
    v.comparison_status.threshold_j <= 0 ||
    !Number.isInteger(v.comparison_status.intervals) ||
    ![0, 1, 2, 3].includes(v.comparison_status.intervals as number) ||
    (v.comparison_status.status === "insufficient_data") !==
      (v.comparison_status.intervals as number) < 3
  )
    throw Error("Invalid route forecast response");
  if (v.status === "available") {
    if (
      !v.configured ||
      v.reason !== null ||
      !count(v.current_fence) ||
      !forecast(v.current, run, asset) ||
      (v.current as RouteForecast).status !== "available" ||
      !finite(v.working_age_s) ||
      v.working_age_s < 0 ||
      !finite(v.real_life_age_s) ||
      v.real_life_age_s < -0.05
    )
      throw Error("Invalid current route forecast");
  } else if (v.current !== null || v.current_fence !== null || !words(v.reason))
    throw Error("Unavailable response exposes current values");
  if (
    v.lifecycle !== null &&
    (!object(v.lifecycle) ||
      !words(v.lifecycle.phase) ||
      !words(v.lifecycle.reason) ||
      !finite(v.lifecycle.generation) ||
      !Number.isSafeInteger(v.lifecycle.generation) ||
      v.lifecycle.generation < 0 ||
      typeof v.lifecycle.updated_at !== "string" ||
      !Number.isFinite(Date.parse(v.lifecycle.updated_at)))
  )
    throw Error("Invalid forecast lifecycle");
  for (const row of v.items) {
    if (
      !object(row) ||
      !count(row.fence) ||
      !forecast(row.payload, run, asset) ||
      !["current", "historical"].includes(String(row.classification)) ||
      !words(row.trigger) ||
      !(row.route_revision === undefined || hash(row.route_revision)) ||
      !(row.reserve_soc == null || fraction(row.reserve_soc)) ||
      typeof row.created_at !== "string" ||
      !Number.isFinite(Date.parse(row.created_at))
    )
      throw Error("Invalid forecast history");
  }
  if (v.diagnostic != null) {
    const d = v.diagnostic;
    if (
      !object(d) ||
      v.status !== "unavailable" ||
      v.reason !== "unserved_energy_or_power" ||
      !count(d.fence) ||
      !forecast(d.payload, run, asset) ||
      (d.payload as RouteForecast).status !== "infeasible" ||
      !finite(v.working_age_s) ||
      v.working_age_s < 0 ||
      !finite(v.real_life_age_s) ||
      v.real_life_age_s < -0.05 ||
      !v.items.some(
        (row) =>
          object(row) &&
          row.fence === d.fence &&
          row.classification === "current" &&
          JSON.stringify(row.payload) === JSON.stringify(d.payload),
      )
    )
      throw Error("Invalid current infeasibility diagnostic");
  }
  for (const row of v.comparisons) {
    if (
      !object(row) ||
      !count(row.fence) ||
      !object(row.payload) ||
      !object(row.payload.scope) ||
      row.payload.scope.run_id !== run ||
      row.payload.scope.asset_id !== asset ||
      !hash(row.payload.input_sha256) ||
      !hash(row.payload.observation_sha256) ||
      ![
        row.payload.from_working_s,
        row.payload.until_working_s,
        row.payload.predicted_net_j,
        row.payload.observed_net_j,
        row.payload.signed_error_j,
      ].every(finite) ||
      typeof row.created_at !== "string" ||
      !Number.isFinite(Date.parse(row.created_at))
    )
      throw Error("Invalid saved forecast comparison");
    const p = row.payload;
    if (
      (p.until_working_s as number) <= (p.from_working_s as number) ||
      Math.abs(
        (p.predicted_net_j as number) -
          (p.observed_net_j as number) -
          (p.signed_error_j as number),
      ) > 1e-6
    )
      throw Error("Forecast comparison does not close");
  }
  return v as unknown as RouteEnergyView;
}

export function routeReason(value: string | null): string {
  const labels: Record<string, string> = {
    forecast_pending: "Waiting for the first forecast",
    source_paused: "Vehicle source paused",
    source_finished: "Working session finished",
    charging_charging:
      "Driving forecast paused during the measured charge transfer",
    charging_unknown:
      "Driving forecast paused until the approved charging stop is complete",
    forecast_disabled: "Route-aware forecasting disabled for this run",
    current_bound_observation_not_ready:
      "Waiting for matching readings and route progress",
    source_stale_or_future: "Current source timing is unavailable",
    obsolete_source_route_or_assignment:
      "Source, route, or assigned inputs changed",
    assigned_route_contains_closed_road_requires_confirmed_detour:
      "A road is closed; waiting for a confirmed detour",
    unserved_energy_or_power:
      "The assumed trip exceeds battery energy or power limits",
  };
  return value ? (labels[value] ?? value.replaceAll("_", " ")) : "Available";
}
