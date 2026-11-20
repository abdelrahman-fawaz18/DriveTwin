import type { HistoryQuery } from "./historyModel.ts";

export function validateSummaryBudget(query: HistoryQuery, bucket: number) {
  const count = Math.max(
    1,
    Math.ceil((query.bounds.to - query.bounds.from) / bucket),
  );
  if (
    !Number.isFinite(bucket) ||
    bucket <= 0 ||
    count * query.signals.length > 5000
  )
    throw Error("Summary exceeds its total interval budget");
}

export function sameHistorySnapshot(
  first: Record<string, unknown> | undefined,
  next: Record<string, unknown>,
) {
  if (!first) return;
  for (const key of [
    "mode",
    "run_id",
    "asset_id",
    "as_of",
    "from_s",
    "to_s",
    "time_basis",
    "policy_sha256",
    "catalog_version",
  ])
    if (first[key] !== next[key])
      throw Error("History metadata changed between signal batches");
}

// The UI has no signal-count cap. Each server request retains its existing bound.
export function historyBatches(query: HistoryQuery): HistoryQuery[] {
  const signals = [...new Set(query.signals)];
  if (signals.some((s) => !query.snap.items.some((i) => i.signal_id === s)))
    throw Error("Unregistered history signal");
  return Array.from({ length: Math.ceil(signals.length / 5) }, (_, i) => ({
    ...query,
    signals: signals.slice(i * 5, i * 5 + 5),
  }));
}
export async function historyJson(
  url: string,
  token: string,
  signal: AbortSignal,
  maximumBytes = 8 * 1024 * 1024,
) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) controller.abort();
  const timer = setTimeout(abort, 15000);
  try {
    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok || !response.body)
      throw Error("History request unavailable");
    const reader = response.body.getReader(),
      chunks: Uint8Array[] = [];
    let bytes = 0;
    try {
      while (true) {
        const result = await reader.read();
        if (result.done) break;
        bytes += result.value.byteLength;
        if (bytes > maximumBytes)
          throw Error("History response exceeds its byte budget");
        chunks.push(result.value);
      }
    } finally {
      await reader.cancel();
      reader.releaseLock();
    }
    const all = new Uint8Array(bytes);
    let offset = 0;
    for (const chunk of chunks) {
      all.set(chunk, offset);
      offset += chunk.length;
    }
    return {
      body: JSON.parse(
        new TextDecoder("utf-8", { fatal: true }).decode(all),
      ) as unknown,
      bytes,
    };
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", abort);
  }
}
