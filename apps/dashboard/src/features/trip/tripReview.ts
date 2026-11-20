export const chargingStages = [
  { id: "approaching", label: "Approaching charger" },
  { id: "waiting_for_charger", label: "Waiting for charger" },
  { id: "charging", label: "Charging observed" },
  { id: "target_reached", label: "Charge target reached" },
  { id: "motion_resumed", label: "Back on route" },
] as const;

const eventKinds = [
  "reserve",
  "declined",
  "approved",
  ...chargingStages.map((stage) => stage.id),
] as const;
export type TripEvent = {
  id: string;
  kind: (typeof eventKinds)[number];
  at: string;
  working: number;
  bay?: string;
};

export function tripEventLabel(event: TripEvent) {
  const label =
    event.kind === "reserve"
      ? "Battery reserve warning"
      : event.kind === "declined"
        ? "Chose to keep driving"
        : event.kind === "approved"
          ? "Charging approval recorded"
          : chargingStages.find((stage) => stage.id === event.kind)?.label;
  return event.bay ? `${label} · ${chargingBayName(event.bay)}` : label;
}

export function chargingBayName(id: string, name?: string) {
  const match = /^(?:dev-)?bay-(\d+)$/i.exec(id);
  const detour = /^detour-bay-(\d+)$/i.exec(id);
  const bay = match
    ? `Charging bay ${match[1]}`
    : detour
      ? `Off-route charging bay ${detour[1]}`
      : id;
  return name && name.toLowerCase() !== bay.toLowerCase() && name !== id
    ? `${name} · ${bay}`
    : bay;
}

export function readTripEvents(raw: string | null): TripEvent[] {
  if (!raw || raw.length > 90_000) return [];
  try {
    const rows: unknown = JSON.parse(raw);
    return Array.isArray(rows)
      ? rows
          .filter(
            (row): row is TripEvent =>
              row !== null &&
              typeof row === "object" &&
              typeof row.id === "string" &&
              row.id.length <= 200 &&
              eventKinds.includes(row.kind) &&
              typeof row.at === "string" &&
              Number.isFinite(Date.parse(row.at)) &&
              Number.isFinite(row.working) &&
              row.working >= 0 &&
              (row.bay === undefined ||
                (typeof row.bay === "string" && row.bay.length <= 150)),
          )
          .slice(-200)
      : [];
  } catch {
    return [];
  }
}

export function addTripEvent(events: TripEvent[], event: TripEvent) {
  return events.some(({ id }) => id === event.id)
    ? events
    : [...events.slice(-199), event];
}
