import { useEffect, useState } from "react";
import { Frames, parseSnapshot } from "./model.ts";
import type { Snapshot } from "./model.ts";
type Connection = "connecting" | "live" | "disconnected";
export function useLive(
  token: string,
  asset: string,
  onClosed: (snapshot: Snapshot) => void,
) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [notice, setNotice] = useState("");
  const [authorized, setAuthorized] = useState(true);
  useEffect(() => {
    setSnap(null);
    setNotice("");
    setAuthorized(true);
    setConnection("connecting");
    if (!token || !asset) return;
    let stopped = false,
      attempt = 0,
      last: Snapshot | null = null;
    let active: AbortController | undefined,
      timer: ReturnType<typeof setTimeout> | undefined;
    const headers = { Authorization: `Bearer ${token}` };
    const onOffline = () => {
      if (stopped) return;
      active?.abort();
      setConnection("disconnected");
      setNotice(
        "Connection interrupted. Last readings are stale while reconnecting.",
      );
    };
    window.addEventListener("offline", onOffline);
    async function loop() {
      while (!stopped) {
        active = new AbortController();
        const watchdog = () => {
          clearTimeout(timer);
          timer = setTimeout(() => active?.abort(), 4500);
        };
        try {
          watchdog();
          const suffix = last
            ? `&cursor=${encodeURIComponent(last.cursor)}`
            : "";
          const response = await fetch(
            `/v1/stream?asset_id=${encodeURIComponent(asset)}${suffix}`,
            { headers, signal: active.signal, cache: "no-store" },
          );
          if (response.status === 401 || response.status === 403) {
            if (!stopped) setAuthorized(false);
            break;
          }
          if (response.status === 409) {
            last = null;
            if (!stopped) setSnap(null);
            throw Error("New snapshot required");
          }
          if (!response.ok || !response.body)
            throw Error("Update service unavailable");
          const reader = response.body.getReader(),
            decoder = new TextDecoder(),
            frames = new Frames();
          let first = true;
          while (!stopped) {
            const part = await reader.read();
            if (part.done) throw Error("Update connection ended");
            for (const frame of frames.push(
              decoder.decode(part.value, { stream: true }),
            )) {
              const next = parseSnapshot(
                JSON.parse(frame),
                asset,
                last?.run_id,
              );
              if (
                last &&
                Number(next.cursor.split(":")[2]) <
                  Number(last.cursor.split(":")[2])
              )
                throw Error("Snapshot revision moved backward");
              if (stopped) return;
              if (next.monitoring_closed) {
                stopped = true;
                window.removeEventListener("offline", onOffline);
                onClosed(next);
                return;
              }
              if (next.resynchronized)
                setNotice(
                  "Resynchronized · replaced display with a consistent snapshot. History retained.",
                );
              else if (first) setNotice("");
              first = false;
              last = next;
              setSnap(next);
              setConnection("live");
              attempt = 0;
              watchdog();
            }
          }
        } catch {
          if (!stopped) {
            setConnection("disconnected");
            setNotice(
              "Connection interrupted. Last readings are stale while reconnecting.",
            );
          }
        } finally {
          clearTimeout(timer);
          active?.abort();
        }
        if (!stopped)
          await new Promise<void>((resolve) => {
            timer = setTimeout(
              resolve,
              Math.min(8000, 500 * 2 ** attempt++) + Math.random() * 200,
            );
          });
      }
    }
    void loop();
    return () => {
      stopped = true;
      active?.abort();
      clearTimeout(timer);
      window.removeEventListener("offline", onOffline);
    };
  }, [token, asset, onClosed]);
  return { snap, connection, notice, authorized };
}
