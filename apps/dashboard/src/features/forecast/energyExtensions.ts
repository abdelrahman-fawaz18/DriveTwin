import type { EnergyValues, EnergyEnvelope } from "./energyModel.ts";

export interface EnergyExplanation {
  version: "energy-explanation/1.0.0";
  horizon_duration_s: number;
  horizon_distance_m: number;
  declared_capacity_kwh: number;
  loaded_mass_kg: number;
  reserve_soc: number;
  auxiliary_power_w: number;
  grade_assumption: string;
  motion_assumption: string;
  capacity_basis: string;
  external_charging_basis: string;
  conditional_reference_assumption: string;
}
export interface StationAssessment {
  station_id: string;
  hypothetical: true;
  availability: "unverified";
  status:
    | "unavailable"
    | "reachable_under_assumptions"
    | "at_station_approach"
    | "reserve_shortfall"
    | "energy_shortfall";
  reason_codes: string[];
  values: EnergyValues | null;
  uncertainty:
    | (EnergyEnvelope & { station_endpoint_coverage_measured: false })
    | null;
  path: null | {
    remaining_distance_m: number;
    horizon_duration_s: number;
    edges: string[];
    approach_lane_id: string;
    motion_assumption: string;
    physical_site_access: "unverified";
  };
}
export interface EnergyExtensions {
  version: "energy-mission-extensions/1.0.0";
  explanation: EnergyExplanation | null;
  stations: {
    version: "station-assessments/1.0.0";
    profile_sha256: string | null;
    items: StationAssessment[];
    reason_codes: string[];
  };
}
const object = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const finite = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v);
const strings = (v: unknown) =>
  Array.isArray(v) && v.every((s) => typeof s === "string");
export function checkExtensions(
  value: unknown,
  values: (x: unknown) => boolean,
  envelope: (x: unknown) => boolean,
): void {
  if (
    !object(value) ||
    value.version !== "energy-mission-extensions/1.0.0" ||
    !object(value.stations) ||
    value.stations.version !== "station-assessments/1.0.0" ||
    !strings(value.stations.reason_codes) ||
    !(
      value.stations.profile_sha256 === null ||
      (typeof value.stations.profile_sha256 === "string" &&
        /^[a-f0-9]{64}$/.test(value.stations.profile_sha256))
    ) ||
    !Array.isArray(value.stations.items) ||
    value.stations.items.length > 20
  )
    throw Error("Invalid mission extension");
  const e = value.explanation;
  if (
    e !== null &&
    (!object(e) ||
      e.version !== "energy-explanation/1.0.0" ||
      ![
        "horizon_duration_s",
        "horizon_distance_m",
        "declared_capacity_kwh",
        "loaded_mass_kg",
        "reserve_soc",
        "auxiliary_power_w",
      ].every((k) => finite(e[k]) && Number(e[k]) >= 0) ||
      Number(e.reserve_soc) > 1 ||
      ![
        "grade_assumption",
        "motion_assumption",
        "capacity_basis",
        "external_charging_basis",
        "conditional_reference_assumption",
      ].every((k) => typeof e[k] === "string"))
  )
    throw Error("Invalid forecast explanation");
  const seen = new Set<string>();
  for (const item of value.stations.items) {
    if (
      !object(item) ||
      typeof item.station_id !== "string" ||
      seen.has(item.station_id) ||
      item.hypothetical !== true ||
      item.availability !== "unverified" ||
      !strings(item.reason_codes) ||
      ![
        "unavailable",
        "reachable_under_assumptions",
        "at_station_approach",
        "reserve_shortfall",
        "energy_shortfall",
      ].includes(String(item.status))
    )
      throw Error("Invalid station assessment");
    seen.add(item.station_id);
    if (
      item.status === "unavailable"
        ? item.values !== null || item.uncertainty !== null
        : !values(item.values) || !object(item.path)
    )
      throw Error("Invalid station values");
    if (
      item.path !== null &&
      (!object(item.path) ||
        !finite(item.path.remaining_distance_m) ||
        item.path.remaining_distance_m < 0 ||
        !finite(item.path.horizon_duration_s) ||
        item.path.horizon_duration_s < 0 ||
        !strings(item.path.edges) ||
        !Array.isArray(item.path.edges) ||
        item.path.edges.length > 2000 ||
        typeof item.path.approach_lane_id !== "string" ||
        typeof item.path.motion_assumption !== "string" ||
        item.path.physical_site_access !== "unverified")
    )
      throw Error("Invalid station path");
    if (
      item.uncertainty !== null &&
      (!envelope(item.uncertainty) ||
        !object(item.uncertainty) ||
        item.uncertainty.station_endpoint_coverage_measured !== false)
    )
      throw Error("Invalid station uncertainty");
  }
}
