import type { Reading } from "../../monitoring/model.ts";

// These four optional summary cards do not define the generic measurement table.
// Convert only a matching declared storage unit; never infer a unit from a value.
export const summaryCards = [
  {
    signal: "battery.pack.soc",
    title: "State of charge",
    stored: "1",
    unit: "%",
    scale: 100,
    offset: 0,
  },
  {
    signal: "motion.speed",
    title: "Vehicle speed",
    stored: "m/s",
    unit: "km/h",
    scale: 3.6,
    offset: 0,
  },
  {
    signal: "battery.pack.energy",
    title: "Stored energy",
    stored: "J",
    unit: "kWh",
    scale: 1 / 3_600_000,
    offset: 0,
  },
  {
    signal: "traction.motor.temperature",
    title: "Motor temperature",
    stored: "K",
    unit: "°C",
    scale: 1,
    offset: -273.15,
  },
] as const;

export function summaryReading(
  card: (typeof summaryCards)[number],
  reading?: Reading,
) {
  const raw = reading?.observation?.sample.value;
  const supported = reading?.definition.unit === card.stored;
  return {
    value: raw == null || !supported ? null : raw * card.scale + card.offset,
    unit: card.unit,
    stored: card.stored,
    supported,
  };
}
