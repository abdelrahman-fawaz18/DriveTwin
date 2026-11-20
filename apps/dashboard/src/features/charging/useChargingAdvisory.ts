import { useEffect, useState } from "react";

type Estimate = {
  bay_id: string;
  target_soc: number;
  minimum_soc: number;
  charger_arrival_working_s?: number;
  charger_arrival_soc?: number;
  estimated_grid_energy_j?: number;
  destination_soc?: number;
  wait_working_s: number;
  charging_working_s: number;
  arrival_working_s: number;
  working_expires?: number;
  expires_at_real_life_s?: number;
  crossing_working_s: number | null;
  fallback_bay_id: string | null;
  expires_in_real_life_s: number;
  clientExpiryMs?: number;
  job_fence?: number;
  candidate_sha256?: string;
  fallback_sha256?: string;
  off_route?: boolean;
  detour_sha256?: string;
  fallback_off_route?: boolean;
  fallback_detour_sha256?: string;
  fallback_only?: boolean;
  approval_waiting_for_road?: boolean;
  approval_waiting_for_motion?: boolean;
  past_approval_point?: boolean;
  fallback_past_approval_point?: boolean;
  station_state?: string;
  fallback_station_state?: string;
  fallback_details?: Pick<
    Estimate,
    | "target_soc"
    | "minimum_soc"
    | "wait_working_s"
    | "charging_working_s"
    | "arrival_working_s"
    | "working_expires"
  >;
};

export type Advisory = {
  configured: boolean;
  startup_delay_working_s?: number;
  status?: string;
  estimate?: Estimate | null;
  last_offer?: Pick<Estimate, "bay_id" | "target_soc">;
  recorded?: { cutoff_working_s: number } | null;
  excluded_bays?: { bay_id: string; reasons: string[] }[];
  comparison_expires_in_real_life_s?: number;
  reserve_crossing?: {
    coordinates: [number, number];
    working_s: number;
    expires_in_real_life_s: number;
  };
  operator_control?: boolean;
  can_respond?: boolean;
  operator_recovery?: boolean;
  automatic_qualification?: boolean;
  queue_qualification?: boolean;
  observed_effect_stale?: true;
  observed_effect?: {
    state:
      | "approaching"
      | "waiting_for_charger"
      | "charging"
      | "target_reached"
      | "motion_resumed";
    bay_id: string;
    delivered_j: number;
    observed_working_s: number;
  } | null;
};

export function useChargingAdvisory(
  asset: string,
  run: string,
  token: string,
  connected: boolean,
) {
  const [result, setResult] = useState<{
    asset: string;
    run: string;
    token: string;
    data: Advisory;
  } | null>(null);
  const matches = (entry: typeof result): entry is NonNullable<typeof result> =>
    entry?.asset === asset && entry.run === run && entry.token === token;

  useEffect(() => {
    function setData(update: (previous: Advisory | null) => Advisory | null) {
      setResult((previous) => {
        const data = update(matches(previous) ? previous.data : null);
        return data ? { asset, run, token, data } : null;
      });
    }
    if (!connected) {
      setData((previous) =>
        previous?.configured
          ? {
              ...previous,
              status: "source_unavailable",
              estimate: null,
              excluded_bays: undefined,
              observed_effect_stale: true,
              reserve_crossing: undefined,
            }
          : previous,
      );
      return;
    }
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let expiry: ReturnType<typeof setTimeout>;
    let request: AbortController | undefined;

    async function poll() {
      request = new AbortController();
      const timeout = setTimeout(() => request?.abort(), 4000);
      const started = performance.now();
      try {
        const response = await fetch(
          `/v2/charging-advisory?asset_id=${encodeURIComponent(asset)}`,
          {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
            signal: request.signal,
          },
        );
        if (!response.ok) throw new Error("Charging estimate unavailable");
        const next: Advisory = await response.json();
        if (!stopped) {
          clearTimeout(expiry);
          const expirySeconds =
            next.estimate?.expires_in_real_life_s ??
            next.reserve_crossing?.expires_in_real_life_s ??
            next.comparison_expires_in_real_life_s;
          const remaining =
            (expirySeconds ?? 0) * 1000 - (performance.now() - started);
          const historical: Advisory = {
            ...next,
            status: "historical",
            estimate: null,
            excluded_bays: undefined,
            reserve_crossing: undefined,
          };
          function display(value: Advisory) {
            const offer = next.estimate;
            setData((previous) => ({
              ...value,
              last_offer: offer
                ? { bay_id: offer.bay_id, target_soc: offer.target_soc }
                : previous?.last_offer,
            }));
          }
          if (expirySeconds !== undefined && remaining <= 0) {
            display(historical);
          } else {
            display(
              next.estimate && expirySeconds !== undefined
                ? {
                    ...next,
                    estimate: {
                      ...next.estimate,
                      clientExpiryMs: performance.now() + remaining,
                    },
                  }
                : next,
            );
            if (expirySeconds !== undefined) {
              expiry = setTimeout(() => display(historical), remaining);
            }
          }
          if (!next.configured) stopped = true;
        }
      } catch {
        if (!stopped) {
          clearTimeout(expiry);
          setData((previous) =>
            previous?.configured
              ? {
                  ...previous,
                  status: "source_unavailable",
                  estimate: null,
                  excluded_bays: undefined,
                  observed_effect_stale: true,
                  reserve_crossing: undefined,
                }
              : previous,
          );
        }
      } finally {
        clearTimeout(timeout);
        if (!stopped) timer = setTimeout(poll, 1000);
      }
    }

    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearTimeout(expiry);
      request?.abort();
    };
  }, [asset, run, connected, token]);

  return matches(result) ? result.data : null;
}
