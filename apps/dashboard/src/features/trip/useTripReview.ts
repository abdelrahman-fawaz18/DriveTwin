import { useCallback, useEffect, useState } from "react";
import { addTripEvent, readTripEvents, type TripEvent } from "./tripReview.ts";

function stored(key: string) {
  try {
    return readTripEvents(sessionStorage.getItem(key));
  } catch {
    return [];
  }
}

export function useTripReview(run: string, asset: string, enabled: boolean) {
  const key = enabled && run && asset ? `evdt.trip-review:${run}:${asset}` : "";
  const [log, setLog] = useState<{ key: string; events: TripEvent[] }>({
    key: "",
    events: [],
  });
  const events = log.key === key ? log.events : [];
  useEffect(() => {
    if (!key) return;
    setLog((previous) =>
      previous.key === key ? previous : { key, events: stored(key) },
    );
  }, [key]);
  useEffect(() => {
    if (!key || log.key !== key || !log.events.length) return;
    try {
      sessionStorage.setItem(key, JSON.stringify(log.events));
    } catch {
      // Continue with the in-memory review when local storage is unavailable.
    }
  }, [key, log]);
  const record = useCallback(
    (event: Omit<TripEvent, "at">) => {
      if (!key) return;
      const entry = { ...event, at: new Date().toISOString() };
      setLog((previous) => {
        const existing = previous.key === key ? previous.events : stored(key);
        const updated = addTripEvent(existing, entry);
        return previous.key === key && existing === updated
          ? previous
          : { key, events: updated };
      });
    },
    [key],
  );
  return { events, record };
}
