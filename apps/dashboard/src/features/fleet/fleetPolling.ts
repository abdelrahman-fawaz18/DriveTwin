import { parseSnapshot } from "../../monitoring/model.ts";
import type { Snapshot } from "../../monitoring/model.ts";

export interface FleetAsset {
  snap: Snapshot | null;
  connection: "connecting" | "live" | "disconnected";
}

// Two bounded workers share a cycle. Each request carries only one asset scope;
// a replacement is validated before it can replace that asset's last snapshot.
export function startFleetPolling(
  assets: readonly string[],
  token: string,
  update: (asset: string, state: FleetAsset) => void,
  unauthorized: () => void,
  options: {
    fetcher?: typeof fetch;
    intervalMs?: number;
    timeoutMs?: number;
    staleMs?: number;
  } = {},
) {
  const fetcher = options.fetcher ?? fetch;
  const intervalMs = options.intervalMs ?? 250;
  const timeoutMs = options.timeoutMs ?? 3500;
  const staleMs = options.staleMs ?? 4500;
  let stopped = false;
  const active = new Set<AbortController>();
  const latest = new Map<string, Snapshot>();
  const seen = new Map<string, number>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const stop = () => {
    stopped = true;
    clearTimeout(timer);
    clearInterval(watchdog);
    for (const controller of active) controller.abort();
  };
  const disconnected = (asset: string) => {
    if (!stopped)
      update(asset, {
        snap: latest.get(asset) ?? null,
        connection: "disconnected",
      });
  };
  const watchdog = setInterval(
    () => {
      for (const asset of assets)
        if (performance.now() - (seen.get(asset) ?? -Infinity) > staleMs)
          disconnected(asset);
    },
    Math.min(1000, staleMs),
  );
  async function request(asset: string) {
    const controller = new AbortController();
    active.add(controller);
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetcher(
        `/v1/snapshot?asset_id=${encodeURIComponent(asset)}`,
        {
          headers: { Authorization: `Bearer ${token}` },
          signal: controller.signal,
          cache: "no-store",
        },
      );
      if (stopped) return;
      if (response.status === 401 || response.status === 403) {
        stop();
        unauthorized();
        return;
      }
      if (!response.ok) throw Error("Fleet snapshot unavailable");
      // Limit retained response bytes before parsing, including chunked replies.
      if (!response.body) throw Error("Missing fleet snapshot");
      const reader = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        while (true) {
          const part = await reader.read();
          if (part.done) break;
          size += part.value.byteLength;
          if (size > 8 * 1024 * 1024)
            throw Error("Fleet snapshot exceeds response bound");
          chunks.push(part.value);
        }
      } finally {
        await reader.cancel();
      }
      if (stopped) return;
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.byteLength;
      }
      const old = latest.get(asset);
      const next = parseSnapshot(
        JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)),
        asset,
        old?.run_id,
      );
      if (
        old &&
        Number(next.cursor.split(":")[2]) < Number(old.cursor.split(":")[2])
      )
        throw Error("Fleet snapshot revision moved backward");
      latest.set(asset, next);
      seen.set(asset, performance.now());
      if (next.monitoring_closed) stop();
      update(asset, { snap: next, connection: "live" });
    } catch {
      disconnected(asset);
    } finally {
      clearTimeout(timeout);
      active.delete(controller);
    }
  }
  async function cycle() {
    const start = performance.now();
    let index = 0;
    async function worker() {
      while (!stopped && index < assets.length) {
        const asset = assets[index++]!;
        await request(asset);
      }
    }
    await Promise.all([worker(), worker()]);
    if (!stopped)
      timer = setTimeout(
        () => void cycle(),
        Math.max(0, intervalMs - (performance.now() - start)),
      );
  }
  for (const asset of assets)
    update(asset, { snap: null, connection: "connecting" });
  void cycle();
  return stop;
}
