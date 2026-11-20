// Small deterministic test data. Not a measured trip or a distributable case.
import { createHash } from "node:crypto";
import type {
  DemoCase,
  Forecast,
  Frame,
  Timeline,
} from "../src/features/demo/demoModel.ts";

export function fixture(): DemoCase {
  const ids = [
    "location.latitude",
    "location.longitude",
    "motion.speed",
    "motion.distance",
    "motion.heading",
    "motion.acceleration",
    "location.horizontal_accuracy",
    "battery.pack.soc",
    "battery.pack.voltage",
    "battery.pack.current",
    "battery.pack.power",
    "battery.pack.energy",
    "battery.pack.temperature",
    "traction.motor.speed",
    "traction.motor.torque",
    "traction.motor.temperature",
    "ambient.temperature",
    "auxiliary.power",
    "hvac.power",
    "braking.pressure",
  ];
  const units = [
    "deg",
    "deg",
    "m/s",
    "m",
    "rad",
    "m/s2",
    "m",
    "1",
    "V",
    "A",
    "W",
    "J",
    "K",
    "rad/s",
    "N.m",
    "K",
    "K",
    "W",
    "W",
    "Pa",
  ];
  const frame = (t: number, charge = false): Frame => [
    t,
    [
      45.49912 + t / 100000,
      -73.56653,
      5,
      t * 5,
      0,
      0,
      0.5,
      charge ? 0.116 + t / 10000 : 0.116 - t / 1000,
      650,
      12,
      8000,
      146160000,
      298.15,
      50,
      100,
      300.15,
      293.15,
      8000,
      5000,
      0,
    ],
  ];
  const forecast = (cutoff: number, available: number): Forecast => ({
    id: `forecast-${cutoff}`,
    available,
    cutoff,
    model: "interface-test/1.0.0",
    inputId: `input-${cutoff}`,
    routeId: "route-1",
    trigger: "periodic",
    status: "available",
    reason: null,
    summary: {
      arrival: 0.09,
      minimum: 0.085,
      duration: 60 - cutoff,
      distance: 3500,
      energyKwh: 8,
      drawKwh: 10,
      recoveryKwh: 2,
      auxKwh: 1,
      capacityKwh: 350,
      assumptions: ["Interface test fixture; no accuracy claim"],
      ledger: {
        initial_energy_j: 146160000,
        capacity_j: 1260000000,
        net_battery_j: 28800000,
        battery_draw_j: 36000000,
        battery_recovery_j: 7200000,
        auxiliary_required_j: 3600000,
        propulsion_required_j: 32400000,
        regeneration_used_j: 7200000,
        unserved_energy_j: 0,
        rejected_regeneration_j: 0,
        reserve_breached: true,
        motion_feasible: true,
        assumptions: ["Interface test fixture; no accuracy claim"],
      },
    },
  });
  const common: Timeline = {
    frames: Array.from({ length: 30 }, (_, i) => frame(i + 1)),
    forecasts: [forecast(1, 2)],
    progress: [{ t: 1, remainingM: 3500, atStop: false }],
    events: [],
    transfers: [],
  };
  const charge: Timeline = {
    frames: Array.from({ length: 30 }, (_, i) => frame(i + 31, true)),
    forecasts: [forecast(35, 37)],
    progress: [{ t: 31, remainingM: 3000, atStop: false }],
    events: [
      { t: 31, kind: "approval", title: "Charging approval accepted" },
      { t: 32, kind: "stop_inserted", title: "Charging stop applied" },
      { t: 40, kind: "charging_observed", title: "Charging measured" },
      { t: 50, kind: "target_observed", title: "Charge target reached" },
      { t: 51, kind: "motion_resumed", title: "Driving resumed" },
      { t: 60, kind: "arrived", title: "Trip complete" },
    ],
    transfers: [
      { t: 40, state: "charging", deliveredKwh: 1 },
      { t: 51, state: "motion_resumed", deliveredKwh: 4 },
    ],
    summary: {
      duration: 60,
      arrivalSoc: frame(60, true)[1][7]!,
      minimumSoc: 0.101,
      deliveredKwh: 4,
      reserveCrossing: null,
      completed: true,
    },
  };
  const continuing: Timeline = {
    frames: Array.from({ length: 30 }, (_, i) => frame(i + 31)),
    forecasts: [forecast(35, 37)],
    progress: [],
    events: [
      { t: 31, kind: "reserve", title: "Measured charge below 10% reserve" },
      { t: 60, kind: "arrived", title: "Trip complete" },
    ],
    transfers: [],
    summary: {
      duration: 60,
      arrivalSoc: frame(60)[1][7]!,
      minimumSoc: 0.056,
      deliveredKwh: 0,
      reserveCrossing: 31,
      completed: true,
    },
  };
  const map = {
    type: "FeatureCollection" as const,
    context_version: "1.0.0",
    run_id: "browser-demo",
    asset_id: "bus-001",
    mission_id: "interface-test",
    bbox: [-73.57, 45.499, -73.56, 45.51] as [number, number, number, number],
    attribution: "OpenStreetMap contributors · ODbL-1.0",
    input_sha256: {},
    features: [
      {
        type: "Feature" as const,
        properties: {
          id: "station-1",
          layer: "stations",
          name: "Charging bay 2",
        },
        geometry: { type: "Point" as const, coordinates: [-73.56653, 45.5] },
      },
      {
        type: "Feature" as const,
        properties: { id: "departure", layer: "endpoints", name: "Departure" },
        geometry: {
          type: "Point" as const,
          coordinates: [-73.56653, 45.49912],
        },
      },
    ],
  };
  return {
    manifest: {
      version: "branched-trip-demo/1.1.0",
      id: "interface-test",
      title: "Interface test fixture · not a qualified trip",
      region: "Montréal",
      basis: "Interface test fixture",
      initialSoc: 0.116,
      reserveSoc: 0.1,
      decision: {
        at: 30,
        stationId: "station-1",
        stationName: "Charging bay 2",
        targetSoc: 0.12,
        chargingSeconds: 95.4,
        minimumSocWithStop: 0.105,
        arrivalSocWithStop: 0.11,
        arrivalWorkingS: 60,
        waitSeconds: 10,
        energyKwh: 4.5,
        choices: ["charge", "continue"],
      },
      files: {} as DemoCase["manifest"]["files"],
      attribution: [
        "OpenStreetMap contributors · ODbL-1.0",
        "Functional test data; not a release case",
      ],
      evidence: {
        kind: "Interface test fixture; not a measured outcome",
        prefixSha256: "a".repeat(64),
        sourceRevision: "b".repeat(64),
      },
    },
    catalog: ids.map((id, i) => ({
      id,
      unit: units[i]!,
      freshness: 5,
      provenance: "Interface test fixture",
    })),
    map,
    common,
    charge,
    continue: continuing,
  };
}
export function fixtureFiles(data = fixture()) {
  const assets: Record<string, Buffer> = {};
  for (const key of [
    "catalog",
    "map",
    "common",
    "charge",
    "continue",
  ] as const) {
    const raw = Buffer.from(JSON.stringify(data[key]) + "\n");
    data.manifest.files[key] = {
      path: key + ".json",
      bytes: raw.length,
      sha256: createHash("sha256").update(raw).digest("hex"),
    };
    assets[key + ".json"] = raw;
  }
  assets["manifest.json"] = Buffer.from(JSON.stringify(data.manifest));
  return assets;
}
