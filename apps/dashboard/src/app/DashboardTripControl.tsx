import { useEffect, useRef, useState } from "react";
import { Spinner } from "../ui/Loading.tsx";

const endpoint = "http://127.0.0.1:18760/api/dashboard-trip-control";
const keyName = "drivetwin-trip-control";

export function readTripControlKey(): string {
  if (location.origin !== "http://127.0.0.1:18765") return "";
  const match = /^#trip-control=([A-Za-z0-9_-]{43})$/.exec(location.hash);
  if (match)
    history.replaceState(null, "", location.pathname + location.search);
  try {
    if (match) sessionStorage.setItem(keyName, match[1]!);
    return sessionStorage.getItem(keyName) ?? "";
  } catch {
    return match?.[1] ?? "";
  }
}

type Control = {
  run_id: string;
  asset_id: string;
  pause: {
    available: boolean;
    revision: number;
    paused: boolean;
    pending: boolean;
    requested_paused: boolean;
    confirmation_delayed: boolean;
  };
};

export function DashboardTripControl({
  accessKey,
  run,
  asset,
}: {
  accessKey: string;
  run?: string;
  asset: string;
}) {
  const [data, setData] = useState<Control | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const mutation = useRef<AbortController | null>(null);

  async function request(
    signal: AbortSignal,
    change?: { revision: number; paused: boolean },
  ) {
    const response = await fetch(endpoint, {
      signal,
      cache: "no-store",
      credentials: "omit",
      headers: {
        Authorization: `Bearer ${accessKey}`,
        ...(change && { "Content-Type": "application/json" }),
      },
      ...(change && { method: "POST", body: JSON.stringify(change) }),
    });
    if (response.status === 401) return null;
    if (!response.ok)
      throw Error(
        "Trip control could not be confirmed. Check the launcher or wait for the next update.",
      );
    const next: Control = await response.json();
    return next.run_id === run && next.asset_id === asset ? next : null;
  }

  useEffect(() => {
    setData(null);
    setBusy(false);
    setError("");
    if (!accessKey || !run) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let active: AbortController;
    async function poll() {
      const revision = generation.current;
      if (!mutation.current) {
        active = new AbortController();
        const timeout = setTimeout(() => active.abort(), 4500);
        try {
          const next = await request(active.signal);
          if (!stopped && revision === generation.current) {
            setData(next);
            if (next === null) return;
          }
        } catch {
          if (!stopped && revision === generation.current) setData(null);
        } finally {
          clearTimeout(timeout);
        }
      }
      if (!stopped) timer = setTimeout(poll, 1000);
    }
    void poll();
    return () => {
      stopped = true;
      generation.current++;
      clearTimeout(timer);
      active?.abort();
      mutation.current?.abort();
      mutation.current = null;
    };
  }, [accessKey, run, asset]);

  const pause =
    data?.run_id === run && data?.asset_id === asset ? data.pause : null;
  async function toggle() {
    if (!pause?.available || pause.pending || mutation.current) return;
    const own = new AbortController();
    mutation.current = own;
    generation.current++;
    setBusy(true);
    setError("");
    const timeout = setTimeout(() => own.abort(), 4500);
    try {
      const next = await request(own.signal, {
        revision: pause.revision,
        paused: !pause.paused,
      });
      if (mutation.current === own) setData(next);
    } catch (error) {
      if (mutation.current === own) {
        setData(null);
        setError(
          error instanceof Error && error.name !== "AbortError"
            ? error.message
            : "Waiting for updated trip state.",
        );
      }
    } finally {
      clearTimeout(timeout);
      if (mutation.current === own) {
        mutation.current = null;
        setBusy(false);
      }
    }
  }

  if (!pause?.available && !error) return null;
  const waiting = busy || pause?.pending;
  const resuming =
    waiting && (pause?.pending ? !pause.requested_paused : !!pause?.paused);
  return (
    <div className="trip-toolbar-control">
      {pause?.available && (
        <button
          className="secondary"
          disabled={waiting}
          aria-busy={waiting}
          onClick={() => void toggle()}
        >
          {waiting && <Spinner />}
          {waiting
            ? resuming
              ? "Resuming…"
              : "Pausing…"
            : pause.paused
              ? "Resume trip"
              : "Pause trip"}
        </button>
      )}
      {(error || pause?.confirmation_delayed) && (
        <span className="fine" role="status">
          {error || "Waiting for simulator confirmation."}
        </span>
      )}
    </div>
  );
}
