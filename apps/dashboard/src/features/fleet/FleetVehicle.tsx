import { Fragment, memo } from "react";
import { time, value } from "../../monitoring/model.ts";
import type { FleetAsset } from "./fleetPolling.ts";
import {
  signalGroup,
  groupTone,
  measurementName,
} from "../../app/presentation.ts";
import { Loading } from "../../ui/Loading.tsx";
export const FleetVehicle = memo(function Vehicle({
  asset,
  state,
  open,
  recorded = false,
}: {
  asset: string;
  state: FleetAsset | undefined;
  open: (asset: string) => void;
  recorded?: boolean;
}) {
  const snap = state?.snap;
  const connection = state?.connection ?? "connecting";
  return (
    <section
      className="panel fleet-vehicle"
      data-fleet-asset={asset}
      data-connection={recorded ? "recorded" : connection}
      aria-label={`Fleet vehicle ${asset}`}
    >
      <div className="panel-head">
        <div>
          <p className="eyebrow">
            {recorded ? "RECORDED VEHICLE" : "AUTHORIZED VEHICLE"}
          </p>
          <h2>{asset}</h2>
          <p className="fleet-status" role="status">
            <span className={`connection-dot ${connection}`} />{" "}
            {recorded
              ? "Recorded"
              : connection === "live"
                ? "Connected"
                : connection === "connecting"
                  ? "Connecting…"
                  : "Disconnected · last readings stale"}
            {snap &&
              (recorded
                ? ` · ${value(snap.items[0]?.observation?.sim_time_s)} working s`
                : ` · Updated ${time(snap.as_of)}`)}
          </p>
        </div>
        <button className="secondary" onClick={() => open(asset)}>
          Open {asset} details
        </button>
      </div>
      {!snap ? (
        <p className="empty">
          {connection === "connecting" ? (
            <Loading label="Loading vehicle readings…" />
          ) : (
            "Vehicle readings unavailable."
          )}
        </p>
      ) : (
        <details className="fleet-detail" open>
          <summary>
            {recorded ? "Recorded readings" : "Live readings"} ·{" "}
            {snap.items.length} signals
          </summary>
          <div className="table-scroll">
            <table className="fleet-table">
              <caption className="sr-only">
                {asset} {recorded ? "recorded" : "live"} measurements
              </caption>
              <thead>
                <tr>
                  <th scope="col">Signal</th>
                  <th scope="col">Value · stored unit</th>
                  <th scope="col">Quality</th>
                  <th scope="col">Freshness</th>
                  <th scope="col">Details</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ...new Set(snap.items.map((i) => signalGroup(i.signal_id))),
                ].map((g) => (
                  <Fragment key={g}>
                    <tr className={`group-row ${groupTone(g)}`}>
                      <th colSpan={5}>{g}</th>
                    </tr>
                    {snap.items
                      .filter((i) => signalGroup(i.signal_id) === g)
                      .map((reading) => (
                        <tr
                          key={reading.signal_id}
                          data-signal={reading.signal_id}
                          data-asset={asset}
                          data-run={snap.run_id}
                          data-source={reading.source_id}
                          data-event={reading.observation?.event_id ?? ""}
                        >
                          <td>{measurementName(reading.signal_id)}</td>
                          <td>
                            {value(reading.observation?.sample.value)}{" "}
                            {reading.definition.unit}
                          </td>
                          <td>
                            <span className={`badge ${reading.quality}`}>
                              {reading.quality}
                            </span>
                          </td>
                          <td>
                            <span
                              className={`badge ${connection === "live" ? reading.freshness : "stale"}`}
                            >
                              {recorded
                                ? "Recorded"
                                : connection === "live"
                                  ? reading.freshness
                                  : "stale"}
                            </span>
                          </td>
                          <td>
                            <details>
                              <summary>Measurement metadata</summary>
                              <div className="measurement-detail">
                                <p>Signal: {reading.signal_id}</p>
                                <p>Source: {reading.source_id}</p>
                                <p>
                                  Component: {reading.definition.component_id}
                                </p>
                                <p>
                                  Location:{" "}
                                  {reading.definition.physical_location}
                                </p>
                                <p>Type: {reading.definition.classification}</p>
                                <p>
                                  Provenance:{" "}
                                  {reading.observation?.sample.provenance ??
                                    reading.definition.provenance}
                                </p>
                                <p>Source state: {reading.source_state}</p>
                                <p>
                                  Observed:{" "}
                                  {time(reading.observation?.observed_at)}
                                </p>
                                <p>
                                  Working time:{" "}
                                  {value(reading.observation?.sim_time_s)} s
                                </p>
                                <p>
                                  Cadence: {reading.definition.sample_period_s}{" "}
                                  s · freshness deadline:{" "}
                                  {reading.definition.freshness_s} s
                                </p>
                                <p>
                                  Reason:{" "}
                                  {reading.observation?.sample.reason ??
                                    "None declared"}
                                </p>
                                {reading.observation?.sample.raw_value !==
                                  undefined && (
                                  <p>
                                    Diagnostic raw value:{" "}
                                    {value(
                                      reading.observation.sample.raw_value,
                                    )}
                                  </p>
                                )}
                                <p>
                                  Model:{" "}
                                  {reading.definition.model_version ??
                                    "Not declared"}
                                </p>
                                <p>{reading.definition.evidence_class}</p>
                              </div>
                            </details>
                          </td>
                        </tr>
                      ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
});
