import { useEffect, useRef, useState } from "react";
import type { SavedRouteForecast } from "../forecast/forecastNavigation.ts";
import { mergeTrajectory, parseTrajectory, type Fix } from "./mapModel.ts";
import type { Snapshot } from "../../monitoring/model.ts";

const recentSeconds = 1800;
const maxTripSeconds = 86400;
export type TrajectoryWindow = "full" | "recent";

export function useObservedTrajectory(
  snap: Snapshot,
  token: string,
  window: TrajectoryWindow,
  forecast?: SavedRouteForecast | null,
) {
  const latest = useRef(snap);
  latest.current = snap;
  const [track, setTrack] = useState({ points: [] as Fix[], maxGap: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    if (snap.monitoring_closed) {
      setLoading(false);
      return;
    }
    const full = !forecast && window === "full";
    const maxPoints = full ? 20_000 : 5_000;
    let stopped = false;
    let pending: AbortController | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let previousCursor: string | undefined;
    let points: Fix[] = [];
    setTrack({ points, maxGap: 0 });
    setError("");
    setLoading(true);

    async function load() {
      const current = latest.current;
      if (full && current.cursor === previousCursor) {
        timer = setTimeout(load, 4000);
        return;
      }
      const request = new AbortController();
      pending = request;
      const timeout = setTimeout(() => request.abort(), full ? 30_000 : 10_000);
      const end = full
        ? maxTripSeconds
        : (forecast?.payload.cutoff_working_s ??
          Math.max(
            0,
            ...current.items.map(
              (reading) => reading.observation?.sim_time_s ?? 0,
            ),
          ));
      const query = new URLSearchParams({
        asset_id: current.asset_id,
        as_of: current.cursor,
        from_s: String(full ? 0 : Math.max(0, end - recentSeconds)),
        to_s: String(end),
        limit: full ? "1000" : "500",
      });
      if (full && previousCursor) query.set("since", previousCursor);
      const incoming: Fix[] = [];
      let next: string | null = null;
      let maxGap = 0;
      let seen = 0;
      try {
        if (
          full &&
          current.items.some(
            (reading) =>
              (reading.observation?.sim_time_s ?? 0) > maxTripSeconds,
          )
        ) {
          setError(
            "The full-trip view covers one working day. Select Last 30 working minutes to follow the bus.",
          );
          return;
        }
        do {
          if (next) query.set("after", next);
          const response = await fetch("/v1/trajectory?" + query, {
            headers: { Authorization: `Bearer ${token}` },
            signal: request.signal,
            cache: "no-store",
          });
          if (!response.ok) throw Error();
          const page = parseTrajectory(
            await response.json(),
            current,
            current.cursor,
          );
          seen += page.items.length;
          if (
            seen > maxPoints ||
            (page.next && (!page.items.length || page.next === next))
          )
            throw Error();
          incoming.push(
            ...page.items.filter(
              (point) =>
                !forecast ||
                (point.source_id === forecast.payload.scope.source_id &&
                  point.source_epoch === forecast.payload.scope.source_epoch &&
                  point.sim_time_s <= end),
            ),
          );
          next = page.next;
          maxGap = page.max_gap_s;
        } while (next);
        if (stopped) return;
        const updated = full ? mergeTrajectory(points, incoming) : incoming;
        if (updated.length > maxPoints) throw Error();
        points = updated;
        previousCursor = current.cursor;
        setTrack({ points, maxGap });
        setError("");
      } catch {
        if (!stopped)
          setError(
            points.length
              ? "Trajectory update unavailable. Showing the last loaded trail."
              : `Trajectory unavailable or exceeds the ${maxPoints.toLocaleString("en-US")}-point limit.`,
          );
      } finally {
        clearTimeout(timeout);
        if (!stopped) {
          setLoading(false);
          if (!forecast) timer = setTimeout(load, 4000);
        }
      }
    }
    void load();
    return () => {
      stopped = true;
      clearTimeout(timer);
      pending?.abort();
    };
  }, [
    snap.run_id,
    snap.asset_id,
    token,
    forecast,
    window,
    snap.monitoring_closed,
  ]);
  return { ...track, loading, error };
}
