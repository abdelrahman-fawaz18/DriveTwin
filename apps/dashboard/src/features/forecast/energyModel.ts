import { checkExtensions } from "./energyExtensions.ts";
import type { EnergyExtensions } from "./energyExtensions.ts";

export interface EnergyValues {
  net_mission_energy_kwh: number;
  projected_final_soc: number;
  reserve_feasible: boolean;
  energy_plan_feasible: boolean;
  conditional_distance_to_reserve_m: number | null;
  conditional_time_to_reserve_s: number | null;
}
export interface EnergyEnvelope {
  method: string;
  minimum_energy_kwh: number;
  maximum_energy_kwh: number;
  minimum_final_soc: number;
  maximum_final_soc: number;
  probability_guarantee: false;
}
export interface EnergyRecord {
  extensions?: EnergyExtensions;
  record_id: string;
  status: "advisory" | "unavailable";
  reason_codes: string[];
  model_version: string;
  numerical_model_version: string;
  release_sha256: string;
  values: EnergyValues | null;
  uncertainty: EnergyEnvelope | null;
  observation_references: { event_id: string; wire_sha256: string }[];
  source_clock: { sim_time_s: number } | null;
  model_qualified: false;
  automatic_dispatch_approved: false;
}
export interface EnergyView {
  input_age_s?: number | null;
  reference_values?: EnergyValues | null;
  extensions?: EnergyExtensions | null;
  version: "energy-forecast-view/1.0.0";
  run_id: string;
  asset_id: string;
  as_of: string;
  status: "advisory" | "unavailable";
  reason_codes: string[];
  values: EnergyValues | null;
  uncertainty: EnergyEnvelope | null;
  current_record_id: string | null;
  items: { sequence: number; created_at: string; payload: EnergyRecord }[];
  next: number | null;
}
const object = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const finite = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v);
const hash = (v: unknown): v is string =>
  typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const strings = (v: unknown) =>
  Array.isArray(v) && v.every((s) => typeof s === "string");
function values(v: unknown): boolean {
  return (
    object(v) &&
    finite(v.net_mission_energy_kwh) &&
    finite(v.projected_final_soc) &&
    v.projected_final_soc >= 0 &&
    v.projected_final_soc <= 1 &&
    typeof v.reserve_feasible === "boolean" &&
    typeof v.energy_plan_feasible === "boolean" &&
    [
      v.conditional_distance_to_reserve_m,
      v.conditional_time_to_reserve_s,
    ].every((n) => n === null || (finite(n) && n >= 0))
  );
}
function envelope(v: unknown): boolean {
  return (
    object(v) &&
    v.method === "training-joint-grid-envelope/1.0.0" &&
    v.probability_guarantee === false &&
    finite(v.minimum_energy_kwh) &&
    finite(v.maximum_energy_kwh) &&
    v.minimum_energy_kwh <= v.maximum_energy_kwh &&
    finite(v.minimum_final_soc) &&
    finite(v.maximum_final_soc) &&
    v.minimum_final_soc >= 0 &&
    v.maximum_final_soc <= 1 &&
    v.minimum_final_soc <= v.maximum_final_soc
  );
}
export function parseEnergy(
  x: unknown,
  run: string,
  asset: string,
): EnergyView {
  if (
    !object(x) ||
    x.version !== "energy-forecast-view/1.0.0" ||
    x.run_id !== run ||
    x.asset_id !== asset ||
    typeof x.as_of !== "string" ||
    !Number.isFinite(Date.parse(x.as_of)) ||
    !["advisory", "unavailable"].includes(String(x.status)) ||
    !strings(x.reason_codes) ||
    !Array.isArray(x.items) ||
    x.items.length > 50 ||
    !(
      x.next === null ||
      (Number.isSafeInteger(x.next) && Number(x.next) > 0)
    ) ||
    x.model_qualified !== false ||
    x.automatic_dispatch_approved !== false
  )
    throw Error("Forecast identity or format changed");
  if (
    x.status === "advisory"
      ? !values(x.values) ||
        !envelope(x.uncertainty) ||
        !hash(x.current_record_id)
      : x.values !== null ||
        x.uncertainty !== null ||
        x.current_record_id !== null
  )
    throw Error("Invalid current forecast values");
  let previous = Infinity;
  if (
    x.input_age_s !== undefined &&
    x.input_age_s !== null &&
    (!finite(x.input_age_s) || x.input_age_s < 0)
  )
    throw Error("Invalid input age");
  if (
    x.reference_values !== undefined &&
    x.reference_values !== null &&
    (x.status !== "advisory" || !values(x.reference_values))
  )
    throw Error("Invalid reference values");
  if (x.extensions !== undefined && x.extensions !== null) {
    if (x.status !== "advisory")
      throw Error("Unavailable current extensions must be hidden");
    checkExtensions(x.extensions, values, envelope);
  }
  for (const row of x.items) {
    if (
      !object(row) ||
      !Number.isSafeInteger(row.sequence) ||
      Number(row.sequence) <= 0 ||
      Number(row.sequence) >= previous ||
      typeof row.created_at !== "string" ||
      !Number.isFinite(Date.parse(row.created_at)) ||
      !object(row.payload)
    )
      throw Error("Invalid forecast history");
    previous = Number(row.sequence);
    const p = row.payload;
    if (p.extensions !== undefined)
      checkExtensions(p.extensions, values, envelope);
    if (
      p.version !== "persistent-energy-forecast/1.0.0" ||
      p.run_id !== run ||
      p.asset_id !== asset ||
      !hash(p.record_id) ||
      !hash(p.release_sha256) ||
      !strings(p.reason_codes) ||
      typeof p.model_version !== "string" ||
      typeof p.numerical_model_version !== "string" ||
      p.model_qualified !== false ||
      p.automatic_dispatch_approved !== false ||
      !Array.isArray(p.observation_references) ||
      p.observation_references.length > 1000 ||
      p.observation_references.some(
        (r) =>
          !object(r) || typeof r.event_id !== "string" || !hash(r.wire_sha256),
      ) ||
      !(
        p.source_clock === null ||
        (object(p.source_clock) && finite(p.source_clock.sim_time_s))
      ) ||
      !["advisory", "unavailable"].includes(String(p.status)) ||
      (p.status === "advisory"
        ? !values(p.values) || !envelope(p.uncertainty)
        : p.values !== null || p.uncertainty !== null)
    )
      throw Error("Invalid stored forecast");
  }
  if (
    x.status === "advisory" &&
    !x.items.some(
      (r) =>
        object(r) &&
        object(r.payload) &&
        r.payload.record_id === x.current_record_id,
    )
  )
    throw Error("Current forecast record missing");
  return x as unknown as EnergyView;
}
export const reasonText = (s: string) => s.replaceAll("_", " ");
