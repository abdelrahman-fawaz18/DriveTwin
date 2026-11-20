import { useEffect, useRef, useState } from "react";
import { parseForecastMap } from "./forecastMap.ts";
import type { SavedRouteForecast } from "../forecast/forecastNavigation.ts";
import { parseContext, type MapContext } from "./mapModel.ts";

export function useTripMap(
  run: string,
  asset: string,
  token: string,
  enabled: boolean,
  forecast?: SavedRouteForecast | null,
) {
  const key = `${run}:${asset}:${forecast ? `${forecast.fence}:${forecast.payload.input_sha256}` : "live"}`;
  const record = useRef(forecast);
  record.current = forecast;
  const [loaded, setLoaded] = useState<{
    key: string;
    context: MapContext | null;
    error: boolean;
  } | null>(null);
  useEffect(() => {
    if (!enabled || !run || !asset || !token) return;
    const abort = new AbortController();
    const selected = record.current;
    const path = selected
      ? `/v2/energy-forecasts/${selected.fence}/map`
      : "/v1/map-context";
    async function load() {
      try {
        const response = await fetch(
          path + "?" + new URLSearchParams({ asset_id: asset }),
          {
            headers: { Authorization: `Bearer ${token}` },
            signal: AbortSignal.any([abort.signal, AbortSignal.timeout(8000)]),
            cache: "no-store",
          },
        );
        if (!response.ok) throw Error("Route unavailable");
        const body = await response.json();
        const context = selected
          ? parseForecastMap(body, selected).context
          : parseContext(body, { run_id: run, asset_id: asset });
        if (context.run_id !== run || context.asset_id !== asset)
          throw Error("Route scope changed");
        if (!abort.signal.aborted) setLoaded({ key, context, error: false });
      } catch {
        if (!abort.signal.aborted)
          setLoaded({ key, context: null, error: true });
      }
    }
    void load();
    return () => abort.abort();
  }, [key, run, asset, token, enabled]);
  return loaded?.key === key ? loaded : null;
}
