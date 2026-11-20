import { useEffect, useState } from "react";
import { parseRouteEnergy, type RouteEnergyView } from "./routeEnergyModel.ts";

export function useRouteEnergy(
  token: string,
  run: string,
  asset: string,
  connected: boolean,
  unauthorized: () => void,
) {
  const [data, setData] = useState<RouteEnergyView | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  const [last, setLast] = useState(0),
    [now, setNow] = useState(performance.now());
  const [error, setError] = useState("");
  useEffect(() => {
    let stopped = false,
      timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | null = null;
    setData(null);
    setLast(0);
    setUnsupported(false);
    setError("");
    if (!token || !run || !asset) return;
    const clock = setInterval(() => setNow(performance.now()), 500);
    async function poll() {
      request = new AbortController();
      const own = request;
      const timeout = setTimeout(() => own.abort(), 4500);
      try {
        const r = await fetch(
          `/v2/energy-forecasts?asset_id=${encodeURIComponent(asset)}`,
          {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
            signal: own.signal,
          },
        );
        if (r.status === 401 || r.status === 403) {
          if (!stopped) unauthorized();
          return;
        }
        if (r.status === 404) {
          if (!stopped) {
            setUnsupported(true);
            setLast(0);
            setError("");
          }
          return;
        }
        if (!r.ok) throw Error();
        const parsed = parseRouteEnergy(await r.json(), run, asset);
        if (!stopped) {
          setData(parsed);
          setLast(performance.now());
          setUnsupported(false);
          setError("");
        }
      } catch {
        if (!stopped) {
          setLast(0);
          setError(
            "Forecast connection unavailable. Current values are hidden.",
          );
        }
      } finally {
        clearTimeout(timeout);
        if (!stopped) timer = setTimeout(poll, 1500);
      }
    }
    void poll();
    return () => {
      stopped = true;
      request?.abort();
      clearTimeout(timer);
      clearInterval(clock);
    };
  }, [token, run, asset, unauthorized]);
  const fresh =
    connected &&
    data?.run_id === run &&
    data.asset_id === asset &&
    last > 0 &&
    Math.max(0, now - last) <= 5000;
  const current = fresh && data?.status === "available" ? data.current : null;
  const diagnostic = fresh ? (data?.diagnostic?.payload ?? null) : null;
  return { data, fresh, current, diagnostic, unsupported, error, last, now };
}

export type RouteEnergyRead = ReturnType<typeof useRouteEnergy>;
