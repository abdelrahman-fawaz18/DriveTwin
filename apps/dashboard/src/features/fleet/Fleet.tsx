import { Bars } from "../../ui/Chart.tsx";
import { SectionHeading } from "../../ui/SectionHeading.tsx";
import { summaryCards, summaryReading } from "../overview/overview.ts";
import { useEffect, useState } from "react";
import { value, type Snapshot } from "../../monitoring/model.ts";
import { startFleetPolling } from "./fleetPolling.ts";
import type { FleetAsset } from "./fleetPolling.ts";

import { FleetVehicle as Vehicle } from "./FleetVehicle.tsx";

export function Fleet({
  token,
  assets,
  unauthorized,
  open,
  onClosed,
  monitoredAsset,
  onMonitored,
  monitoredWarning,
}: {
  token: string;
  assets: string[];
  unauthorized: () => void;
  open: (asset: string) => void;
  onClosed: (snap: Snapshot) => void;
  monitoredAsset: string;
  onMonitored: (state: FleetAsset | null) => void;
  monitoredWarning?: string;
}) {
  const [selected, setSelected] = useState(assets);
  const [states, setStates] = useState<Record<string, FleetAsset>>({});
  const monitored = states[monitoredAsset] ?? null;
  useEffect(() => onMonitored(monitored), [monitored, onMonitored]);
  useEffect(() => {
    setStates({});
    const stop = startFleetPolling(
      [...new Set([...selected, monitoredAsset])],
      token,
      (asset, state) => {
        if (state.snap?.monitoring_closed) onClosed(state.snap);
        else setStates((old) => ({ ...old, [asset]: state }));
      },
      unauthorized,
    );
    const offline = () =>
      setStates((old) =>
        Object.fromEntries(
          Object.entries(old).map(([asset, state]) => [
            asset,
            { ...state, connection: "disconnected" as const },
          ]),
        ),
      );
    window.addEventListener("offline", offline);
    return () => {
      stop();
      window.removeEventListener("offline", offline);
    };
  }, [selected, monitoredAsset, token, unauthorized, onClosed]);
  return (
    <div className="fleet">
      <section className="panel fleet-controls">
        <label>
          Fleet vehicles
          <select
            aria-label="Fleet vehicles"
            multiple
            value={selected}
            onChange={(event) =>
              setSelected(
                Array.from(
                  event.target.selectedOptions,
                  (option) => option.value,
                ),
              )
            }
          >
            {assets.map((asset) => (
              <option key={asset}>{asset}</option>
            ))}
          </select>
        </label>
        <p>
          Showing {selected.length} authorized vehicles. Each panel keeps its
          own identity, quality and freshness. Use Ctrl+click to select
          vehicles. Values are original stored measurements; connections refresh
          independently.
        </p>
      </section>
      {!selected.length && (
        <p className="empty">Select a vehicle to monitor.</p>
      )}

      {selected.length > 0 && (
        <>
          <section className="panel fleet-summary">
            <SectionHeading
              title="Vehicle comparison"
              icon="Fleet"
              tone="blue"
            />
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Vehicle</th>
                    <th scope="col">Charge</th>
                    <th scope="col">Distance traveled</th>
                    <th scope="col">Readings</th>
                    <th scope="col">Trip status</th>
                  </tr>
                </thead>
                <tbody>
                  {selected.map((vehicle) => {
                    const state = states[vehicle];
                    const items = state?.snap?.items ?? [];
                    const fresh = items.filter(
                      (item) => item.freshness === "fresh",
                    ).length;
                    const paused =
                      state?.connection === "live" &&
                      items[0]?.source_state === "paused";
                    const good = (signal: string) =>
                      items.find(
                        (item) =>
                          item.signal_id === signal &&
                          item.quality === "good" &&
                          state?.connection === "live" &&
                          ["fresh", "paused"].includes(item.freshness),
                      )?.observation?.sample.value ?? null;
                    const charge = good("battery.pack.soc");
                    const distance = good("motion.distance");
                    return (
                      <tr key={vehicle}>
                        <th scope="row">
                          <button
                            type="button"
                            className="link-button"
                            onClick={() => open(vehicle)}
                          >
                            {vehicle}
                          </button>
                        </th>
                        <td>
                          {charge === null ? "—" : `${value(charge * 100)}%`}
                        </td>
                        <td>
                          {distance === null
                            ? "—"
                            : `${value(distance / 1000)} km`}
                        </td>
                        <td>
                          {paused
                            ? "Paused"
                            : state?.connection === "live"
                              ? `${fresh} / ${items.length} fresh`
                              : state?.connection === "disconnected"
                                ? "Stale"
                                : "Connecting…"}
                        </td>
                        <td>
                          {vehicle === monitoredAsset && monitoredWarning ? (
                            <span className="badge stale">
                              {monitoredWarning}
                            </span>
                          ) : state?.connection === "live" ? (
                            (items[0]?.source_state ?? "Awaiting vehicle")
                          ) : (
                            "Unavailable"
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
          <div className="fleet-grid connected-fleet">
            <div className="stack fleet-readings">
              {selected.map((asset) => (
                <Vehicle
                  key={asset}
                  asset={asset}
                  state={states[asset]}
                  open={open}
                />
              ))}
            </div>
            <aside className="stack">
              <section className="panel tinted violet">
                <h2>Compare battery charge</h2>
                <Bars
                  maximum={100}
                  unit="%"
                  items={selected.map((asset, n) => {
                    const state = states[asset],
                      reading = state?.snap?.items.find(
                        (i) => i.signal_id === summaryCards[0].signal,
                      );
                    return {
                      label: asset,
                      color: ["#d9277f", "#8552ba", "#368e85"][n % 3]!,
                      value:
                        state?.connection === "live" &&
                        reading?.freshness === "fresh" &&
                        reading.quality === "good"
                          ? summaryReading(summaryCards[0], reading).value
                          : null,
                    };
                  })}
                />
                <p className="fine">
                  Fresh, good readings · battery charge (%)
                </p>
              </section>
              <section className="panel">
                <h2>Vehicle speed</h2>
                <Bars
                  unit="km/h"
                  items={selected.map((asset, n) => {
                    const state = states[asset],
                      reading = state?.snap?.items.find(
                        (i) => i.signal_id === summaryCards[1].signal,
                      );
                    return {
                      label: asset,
                      color: ["#33a8c3", "#368e85"][n % 2]!,
                      value:
                        state?.connection === "live" &&
                        reading?.freshness === "fresh" &&
                        reading.quality === "good"
                          ? summaryReading(summaryCards[1], reading).value
                          : null,
                    };
                  })}
                />
                <p className="fine">
                  Zero indicates a stationary vehicle. Unavailable readings
                  appear as —.
                </p>
              </section>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
