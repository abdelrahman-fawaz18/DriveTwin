import catalog from "../../../../catalog/monitoring-signals.json" with { type: "json" };
import { routePosition } from "./route.ts";

export type Mode =
  | "Running"
  | "Paused"
  | "Stale"
  | "Disconnected"
  | "Missing data"
  | "Invalid reading"
  | "Out of domain";
export type Vehicle = "bus-001" | "bus-002";
export const tabs = [
  "Overview",
  "Fleet",
  "Map",
  "Measurements",
  "History",
  "Energy",
  "Activity",
  "System",
  "Operator",
] as const;
export type Tab = (typeof tabs)[number];
export const modes: Mode[] = [
  "Running",
  "Paused",
  "Stale",
  "Disconnected",
  "Missing data",
  "Invalid reading",
  "Out of domain",
];
export const palette = {
  pink: "#d9277f",
  violet: "#8053c7",
  cyan: "#139cbb",
  amber: "#d79216",
  teal: "#198c80",
  lime: "#7e963b",
  gray: "#637084",
};
const labels: Record<string, string> = {
  "location.latitude": "Latitude",
  "location.longitude": "Longitude",
  "motion.speed": "Vehicle speed",
  "motion.distance": "Distance traveled",
  "motion.heading": "Heading",
  "motion.acceleration": "Acceleration",
  "battery.pack.soc": "Battery charge",
  "battery.pack.voltage": "Pack voltage",
  "battery.pack.current": "Pack current",
  "battery.pack.power": "Pack power",
  "battery.pack.energy": "Stored energy",
  "battery.pack.temperature": "Battery temperature",
  "traction.motor.speed": "Motor speed",
  "traction.motor.torque": "Motor torque",
  "traction.motor.temperature": "Motor temperature",
  "ambient.temperature": "Ambient temperature",
  "auxiliary.power": "Auxiliary power",
  "hvac.power": "HVAC power",
  "location.horizontal_accuracy": "Horizontal accuracy",
  "braking.pressure": "Brake pressure",
};
export const groups = [
  {
    name: "Navigation & motion",
    tone: "pink",
    ids: [
      "location.latitude",
      "location.longitude",
      "motion.speed",
      "motion.distance",
      "motion.heading",
      "motion.acceleration",
    ],
  },
  {
    name: "Battery",
    tone: "violet",
    ids: [
      "battery.pack.soc",
      "battery.pack.voltage",
      "battery.pack.current",
      "battery.pack.power",
      "battery.pack.energy",
      "battery.pack.temperature",
    ],
  },
  {
    name: "Drivetrain",
    tone: "cyan",
    ids: [
      "traction.motor.speed",
      "traction.motor.torque",
      "traction.motor.temperature",
    ],
  },
  {
    name: "Environment & loads",
    tone: "amber",
    ids: ["ambient.temperature", "auxiliary.power", "hvac.power"],
  },
  {
    name: "Position quality & braking",
    tone: "teal",
    ids: ["location.horizontal_accuracy", "braking.pressure"],
  },
];
export interface Signal {
  id: string;
  label: string;
  unit: string;
  component: string;
  color: string;
  source: string;
  period: number;
}
export const signals: Signal[] = Object.entries(catalog.signals).map(
  ([id, definition]) => ({
    id,
    label: labels[id] ?? id,
    unit: definition.unit,
    component: definition.component_id,
    color:
      palette[
        (groups.find((g) => g.ids.includes(id))?.tone ??
          "gray") as keyof typeof palette
      ],
    source: "Vehicle adapter",
    period: definition.sample_period_s,
  }),
);
export const signalById = (id: string) =>
  signals.find((s) => s.id === id) ?? signals[0]!;
export const vehicleName = (id: Vehicle) =>
  id === "bus-001" ? "Bus 001" : "Bus 002";
export const number = (value: number | null, digits = 2) =>
  value == null
    ? "—"
    : value.toLocaleString("en-US", { maximumFractionDigits: digits });
export const clock = (seconds: number) => {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};
export const observationTime = (seconds: number) =>
  new Date(Date.UTC(2026, 0, 1, 14, 30) + seconds * 1000)
    .toISOString()
    .slice(11, 23) + " UTC";

// Deterministic display fixtures. No network or command transport is used.
export function sampleValue(
  id: string,
  time: number,
  vehicle: Vehicle = "bus-001",
): number {
  const offset = vehicle === "bus-002" ? 0.112 : 0;
  const soc = Math.max(0.15, 0.728 - time / 30000 - offset);
  const speed = Math.max(
    0,
    8.5 + 2.5 * Math.sin(time / 12) + Math.sin(time / 3.7),
  );
  const current = 65 + 22 * Math.sin(time / 12);
  const values: Record<string, number> = {
    "location.latitude": routePosition(time)[0],
    "location.longitude": routePosition(time)[1],
    "motion.speed": speed,
    "motion.distance": 3800 + time * 8.5,
    "motion.heading": 5.65,
    "motion.acceleration":
      (2.5 / 12) * Math.cos(time / 12) + Math.cos(time / 3.7) / 3.7,
    "battery.pack.soc": soc,
    "battery.pack.voltage": 600,
    "battery.pack.current": current,
    "battery.pack.power": current * 600,
    "battery.pack.energy": soc * 300 * 3600000,
    "battery.pack.temperature": 304 + Math.sin(time / 60),
    "traction.motor.speed": speed * 16,
    "traction.motor.torque": 250 + 28 * Math.sin(time / 12),
    "traction.motor.temperature":
      335.15 + 6 * (1 - Math.exp(-time / 120)) + 0.3 * Math.sin(time / 22),
    "ambient.temperature": 293.15,
    "auxiliary.power": 1800,
    "hvac.power": 2500,
    "location.horizontal_accuracy": 1.5,
    "braking.pressure": speed < 7 ? 120000 : 0,
  };
  return values[id] ?? 0;
}
export interface Point {
  t: number;
  value: number | null;
}
export function series(
  id: string,
  from: number,
  to: number,
  vehicle: Vehicle,
  gap = false,
): Point[] {
  const start = Math.ceil(from),
    end = Math.floor(to);
  return Array.from({ length: Math.max(0, end - start + 1) }, (_, i) => {
    const t = start + i;
    return {
      t,
      value:
        gap && id === "motion.speed" && t >= 90 && t <= 95
          ? null
          : sampleValue(id, t, vehicle),
    };
  });
}
export function statistics(points: Point[]) {
  const values = points.flatMap((p) => (p.value == null ? [] : [p.value]));
  return {
    count: values.length,
    missing: points.length - values.length,
    min: values.length ? Math.min(...values) : null,
    max: values.length ? Math.max(...values) : null,
    mean: values.length
      ? values.reduce((a, b) => a + b, 0) / values.length
      : null,
  };
}
export const csvCell = (value: string | number | null) => {
  const text = value == null ? "" : String(value);
  const safe =
    typeof value === "string" && /^[=+\-@\t\r]/.test(text) ? "'" + text : text;
  return '"' + safe.replaceAll('"', '""') + '"';
};
export function download(name: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export interface EventRecord {
  id: number;
  t: number;
  title: string;
  detail: string;
  tone: string;
  type: string;
}
export const initialEvents: EventRecord[] = [
  {
    id: 5,
    t: 116,
    title: "Source resumed",
    detail: "Vehicle readings resumed at the registered one-second cadence.",
    tone: "cyan",
    type: "Source",
  },
  {
    id: 4,
    t: 110,
    title: "Source paused",
    detail:
      "The producer reported a pause. The browser connection remained available.",
    tone: "violet",
    type: "Source",
  },
  {
    id: 3,
    t: 96,
    title: "Measurement quality recovered",
    detail: "The vehicle speed reading is valid again.",
    tone: "teal",
    type: "Quality",
  },
  {
    id: 2,
    t: 90,
    title: "Missing reading recorded",
    detail: "Vehicle speed has a gap between working times 90 and 95 seconds.",
    tone: "amber",
    type: "Quality",
  },
  {
    id: 1,
    t: 0,
    title: "Source registered",
    detail: "Twenty measurements were registered for this vehicle.",
    tone: "pink",
    type: "Source",
  },
];
