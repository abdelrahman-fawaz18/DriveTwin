import type { RouteEnergyView } from "./routeEnergyModel.ts";

export type SavedRouteForecast = RouteEnergyView["items"][number];
export interface HistoryEntry {
  signals: string[];
  from?: number;
  to?: number;
  cutoff?: number;
  eventTime?: number;
  forecast?: SavedRouteForecast;
  comparison?: boolean;
}
export const forecastSignals = [
  "battery.pack.soc",
  "battery.pack.energy",
  "motion.speed",
];
export interface ForecastNavigation {
  openHistory?: (entry: HistoryEntry) => void;
  openMap?: (record: SavedRouteForecast) => void;
  openCharging?: () => void;
  reserveCrossingWorking?: number;
  openReserveMap?: () => void;
  initialRecord?: SavedRouteForecast | null;
}
