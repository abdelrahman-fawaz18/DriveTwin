import { parseContext } from "./mapModel.ts";
import type { SavedRouteForecast } from "../forecast/forecastNavigation.ts";

export function parseForecastMap(raw: unknown, record: SavedRouteForecast) {
  const x = raw as {
    version: string;
    fence: number;
    input_sha256: string;
    scope: typeof record.payload.scope;
    cutoff_working_s: number;
    observed_real_life_s: number;
    route_revision: string;
    context: unknown;
  };
  const p = record.payload;
  if (
    !x ||
    x.version !== "forecast-map/1.0.0" ||
    x.fence !== record.fence ||
    x.input_sha256 !== p.input_sha256 ||
    !x.scope ||
    Object.entries(p.scope).some(
      ([key, value]) => x.scope[key as keyof typeof x.scope] !== value,
    ) ||
    x.cutoff_working_s !== p.cutoff_working_s ||
    !Number.isFinite(x.observed_real_life_s) ||
    x.observed_real_life_s < 0 ||
    x.route_revision !== record.route_revision
  )
    throw Error("Forecast map identity changed");
  return { ...x, context: parseContext(x.context, p.scope) };
}
