import type { DemoCase, Frame, Player } from "./demoModel.ts";
import { projectCase } from "./demoModel.ts";
import type { Snapshot, Quality } from "../../monitoring/model.ts";
import type { SavedRouteForecast } from "../forecast/forecastNavigation.ts";

export function recordedSnapshot(
  data: DemoCase,
  player: Player,
  view = projectCase(data, player),
): Snapshot {
  const frame = view.current;
  return {
    version: "1.0.0",
    run_id: "browser-demo",
    asset_id: "bus-001",
    mode: "simulation",
    as_of: "",
    cursor: `browser-demo:bus-001:${view.frames.length}`,
    trip_progress: view.progress
      ? {
          remaining_m: view.progress.remainingM,
          next_stop_id: null,
          at_passenger_stop: view.progress.atStop,
        }
      : undefined,
    items: data.catalog.map((signal, i) => ({
      signal_id: signal.id,
      source_id: "Recorded vehicle",
      quality: frame ? ((frame[2]?.[i] ?? "good") as Quality) : "unknown",
      freshness: !frame
        ? "unknown"
        : player.t - frame[0] <= signal.freshness
          ? "fresh"
          : "stale",
      source_state: "recorded",
      age_s: frame ? player.t - frame[0] : null,
      definition: {
        unit: signal.unit,
        component_id: signal.id.split(".").slice(0, -1).join("."),
        physical_location: "Recorded vehicle",
        classification: "observed",
        provenance: signal.provenance,
        sample_period_s: 1,
        freshness_s: signal.freshness,
      },
      observation: frame
        ? {
            event_id: `observation-${frame[0]}`,
            sim_time_s: frame[0],
            observed_at: "",
            received_at: "",
            sample: { value: frame[1][i]!, provenance: signal.provenance },
          }
        : null,
    })),
  };
}

export function recordedForecasts(
  data: DemoCase,
  player: Player,
  view = projectCase(data, player),
): SavedRouteForecast[] {
  return view.forecasts
    .filter((f) => !f.summary || f.summary.ledger)
    .map((f, i) => ({
      fence: i + 1,
      created_at: "",
      classification: "recorded",
      trigger: f.trigger,
      route_revision: f.routeId,
      reserve_soc: data.manifest.reserveSoc,
      payload: {
        version: "route-aware-result/2.0.0",
        model_version: f.model,
        input_sha256: f.inputId,
        route_sha256: f.routeId,
        scope: {
          run_id: "browser-demo",
          asset_id: "bus-001",
          source_id: "recorded",
          source_epoch: 1,
          session_id: "recorded",
        },
        generation: i + 1,
        cutoff_working_s: f.cutoff,
        requested_real_life_s: 0,
        status: f.status,
        reason: f.reason,
        uncertainty_method: "not_established",
        summary: f.summary?.ledger
          ? {
              remaining_distance_m: f.summary.distance,
              duration_working_s: f.summary.duration,
              arrival_working_s: f.cutoff + f.summary.duration,
              arrival_soc: f.summary.arrival,
              minimum_soc: f.summary.minimum,
              ledger: f.summary.ledger,
            }
          : null,
      },
    }));
}

export function historyPoints(
  data: DemoCase,
  frames: Frame[],
  id: string,
  from: number,
  to: number,
  factor = 1,
) {
  const index = data.catalog.findIndex((s) => s.id === id),
    result: { t: number; value: number | null }[] = [];
  for (const frame of frames) {
    if (frame[0] < from || frame[0] > to) continue;
    const previous = result.at(-1);
    if (previous && frame[0] - previous.t > data.catalog[index]!.freshness)
      result.push({ t: (previous.t + frame[0]) / 2, value: null });
    result.push({
      t: frame[0],
      value:
        (frame[2]?.[index] ?? "good") === "good" && frame[1][index] != null
          ? frame[1][index]! * factor
          : null,
    });
  }
  return result;
}
