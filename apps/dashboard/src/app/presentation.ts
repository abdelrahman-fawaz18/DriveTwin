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
export const titles: Record<Tab, string> = {
  Overview: "Vehicle overview",
  Fleet: "Fleet measurements",
  Map: "Route and location",
  Measurements: "All measurements",
  History: "History",
  Energy: "Energy Forecast",
  Activity: "Activity",
  System: "System health",
  Operator: "Vehicle operator",
};
export const descriptions: Record<Tab, string> = {
  Overview: "Live readings and vehicle condition",
  Fleet: "Compare readings across authorized vehicles",
  Map: "Observed location, assigned route, and charging bays",
  Measurements: "Every registered reading, with its quality and source",
  History: "Explore recorded readings over a selected time window",
  Energy: "Remaining route energy, battery reserve, and forecast history",
  Activity: "Source events and data-quality changes",
  System: "Delivery, storage, and connection status",
  Operator: "Vehicle controls and independently observed outcomes",
};
export function signalGroup(id: string): string {
  if (id === "location.horizontal_accuracy" || id.startsWith("braking."))
    return "Position quality and braking";
  if (id.startsWith("location.") || id.startsWith("motion."))
    return "Navigation and motion";
  if (id.startsWith("battery.")) return "Battery";
  if (id.startsWith("traction.")) return "Drivetrain";
  if (
    id.startsWith("auxiliary.") ||
    id.startsWith("environment.") ||
    id.startsWith("ambient.") ||
    id.startsWith("hvac.")
  )
    return "Environment and loads";
  return "Additional measurements";
}
export function signalColor(id: string): string {
  return (
    (
      {
        "Navigation and motion": "#33a8c3",
        "Position quality and braking": "#8552ba",
        Battery: "#d9277f",
        Drivetrain: "#bd6c25",
        "Environment and loads": "#368e85",
      } as Record<string, string>
    )[signalGroup(id)] ?? "#68758b"
  );
}

export function groupTone(group: string): string {
  return (
    (
      {
        "Navigation and motion": "pink",
        Battery: "violet",
        Drivetrain: "cyan",
        "Environment and loads": "amber",
        "Position quality and braking": "teal",
      } as Record<string, string>
    )[group] ?? "gray"
  );
}
export function measurementName(id: string): string {
  return (
    (
      {
        "location.latitude": "Latitude",
        "location.longitude": "Longitude",
        "motion.speed": "Vehicle speed",
        "motion.distance": "Distance traveled",
        "motion.heading": "Heading",
        "motion.acceleration": "Acceleration",
        "battery.pack.soc": "State of charge",
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
      } as Record<string, string>
    )[id] ?? id.replaceAll(".", " · ")
  );
}
