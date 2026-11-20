import { Icon } from "../../ui/Icon.tsx";
import { value } from "../../monitoring/model.ts";
import {
  routeReason,
  type RouteForecast,
  type RouteSummary,
  type RouteEnergyView,
} from "./routeEnergyModel.ts";
import {
  forecastSignals,
  type ForecastNavigation,
} from "./forecastNavigation.ts";
import { ForecastChargeTrend } from "./ForecastChargeTrend.tsx";
import "./route-energy.css";
type SavedForecast = RouteEnergyView["items"][number];
export const chargeSeries = [
  { key: "measured", label: "Measured at forecast" },
  { key: "lowest", label: "Lowest predicted" },
  { key: "arrival", label: "Predicted arrival" },
] as const;

export function chargeValues(summary: RouteSummary | null) {
  return {
    measured:
      summary && summary.ledger.capacity_j > 0
        ? Math.min(
            1,
            Math.max(
              0,
              summary.ledger.initial_energy_j / summary.ledger.capacity_j,
            ),
          )
        : null,
    lowest: summary?.minimum_soc ?? null,
    arrival: summary?.arrival_soc ?? null,
  };
}

export const percent = (fraction: number | null) =>
  fraction === null
    ? "—"
    : `${(Math.max(0, Math.min(1, fraction)) * 100).toFixed(2)}%`;

export const kwh = (joules: number) => value(joules / 3_600_000);

export function workingDuration(seconds: number) {
  const rounded = Math.round(seconds);
  const minutes = Math.floor(rounded / 60);
  const remainder = rounded % 60;
  return minutes ? `${minutes} min ${remainder} s` : `${remainder} s`;
}

function InputCoverage({ assumptions }: { assumptions: readonly string[] }) {
  const used = new Set(assumptions);
  const sources = (labels: Record<string, string>, fallback: string) =>
    Object.entries(labels)
      .filter(([key]) => used.has(key))
      .map(([, label]) => label)
      .join(" · ") || fallback;
  const rows = [
    ["Vehicle state", "Charge and speed from source readings"],
    [
      "Elevation",
      sources(
        {
          elevation_terrain: "Terrain data",
          elevation_synthetic: "Synthetic elevation",
          elevation_declared_flat: "Declared flat",
        },
        "Source basis unrecorded",
      ),
    ],
    [
      "Wind",
      sources(
        {
          wind_basis_declared_calm: "Declared calm",
          wind_basis_observed: "Observed",
          wind_basis_forecast: "Issued forecast",
          wind_basis_synthetic: "Synthetic scenario",
        },
        "Source basis unrecorded",
      ),
    ],
    [
      "Air density",
      sources(
        {
          dry_air_density_from_temperature_pressure:
            "From temperature and pressure",
          declared_air_density: "Declared profile",
        },
        "Source basis unrecorded",
      ),
    ],
    [
      "Equipment",
      sources(
        {
          near_horizon_observed_aggregate_auxiliary:
            "Source readings near the bus",
          far_horizon_declared_aggregate_auxiliary: "Declared demand ahead",
          far_horizon_equipment_profile_aggregate: "Equipment model ahead",
        },
        "Source basis unrecorded",
      ),
    ],
    ["Movement", "Assigned roads and stops · Forward speed assumptions"],
  ];
  return (
    <div className="forecast-coverage-strip">
      <span className="badge">{rows[1]![1]}</span>
      <span className="badge">Wind: {rows[2]![1]}</span>
      <span className="badge">Equipment: {rows[4]![1]}</span>
      <details>
        <summary>Forecast details</summary>
        <h3>Input coverage</h3>
        <dl className="route-energy-coverage">
          {rows.map(([label, basis]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{basis}</dd>
            </div>
          ))}
        </dl>
      </details>
    </div>
  );
}

export function ForecastDetails({
  forecast,
  reserve,
  historical,
  record,
  records,
  openMap,
  reserveCrossingWorking,
  openReserveMap,
}: {
  forecast: RouteForecast;
  reserve: number | null;
  historical: boolean;
  record?: SavedForecast | null;
  records: SavedForecast[];
  openMap?: ForecastNavigation["openMap"];
  reserveCrossingWorking?: number;
  openReserveMap?: () => void;
}) {
  const summary = forecast.summary,
    charges = chargeValues(summary);
  if (!summary)
    return (
      <section className="panel">
        <h3>Forecast unavailable</h3>
        <p>{routeReason(forecast.reason)}</p>
      </section>
    );
  const e = summary.ledger,
    impossible = forecast.status === "infeasible";
  const margin =
    reserve === null || charges.lowest === null
      ? null
      : (charges.lowest - reserve) * 100;
  return (
    <>
      {impossible ? (
        <div className="notice warning forecast-outcome">
          <strong>
            Battery energy or power cannot support the planned trip
          </strong>
          <p>
            {kwh(e.unserved_energy_j)} kWh of demand cannot be supplied. Arrival
            charge and duration are unavailable.
          </p>
        </div>
      ) : (
        e.reserve_breached && (
          <div className="notice warning forecast-outcome">
            <strong>Charge is predicted to fall below reserve</strong>
            <p>
              The assigned route is forecast to cross the{" "}
              {reserve === null ? "configured battery" : percent(reserve)}{" "}
              reserve
              {reserveCrossingWorking === undefined
                ? "."
                : ` near working second ${value(reserveCrossingWorking)}.`}
            </p>
            {reserveCrossingWorking !== undefined && openReserveMap && (
              <button
                type="button"
                className="secondary"
                onClick={openReserveMap}
              >
                Locate reserve crossing
              </button>
            )}
          </div>
        )
      )}
      <div className="route-energy-summary">
        <section className="panel route-energy-charge">
          <div className="panel-head">
            <h3>
              <Icon name="battery" /> Battery charge
            </h3>
            <span className="badge">Reserve {percent(reserve)}</span>
          </div>
          <div className="forecast-charge-values">
            {chargeSeries.map(({ key, label }) => (
              <div className={`charge-${key}`} key={key}>
                <small>{label}</small>
                <strong>{percent(charges[key])}</strong>
              </div>
            ))}
          </div>
          <div className="route-energy-charge-plot">
            {chargeSeries.map(({ key, label }) => (
              <div
                className={`route-energy-charge-row charge-${key}`}
                key={key}
                aria-label={`${label}: ${percent(charges[key])}`}
              >
                <div className="route-energy-charge-track" aria-hidden="true">
                  {charges[key] !== null && (
                    <span
                      style={{
                        width: `${Math.max(0, Math.min(100, charges[key] * 100))}%`,
                      }}
                    />
                  )}
                  {reserve !== null && (
                    <i style={{ left: `${reserve * 100}%` }} />
                  )}
                </div>
              </div>
            ))}
          </div>
          <div className="forecast-charge-axis">
            <span>0%</span>
            <span>100%</span>
          </div>
          <div className="route-energy-charge-footer fine">
            {margin !== null && (
              <strong
                className={margin < 0 ? "charge-arrival" : "charge-measured"}
              >
                {Math.abs(margin).toFixed(2)} percentage points{" "}
                {margin < 0 ? "below" : "above"} reserve
              </strong>
            )}
            <span>At lowest predicted charge</span>
          </div>
        </section>
        <section className="panel gradient pink route-energy-remaining">
          <div className="panel-head">
            <h3>
              <Icon name="Energy" /> Remaining route
            </h3>
            {record && openMap && (
              <button onClick={() => openMap(record)}>
                View forecast route
              </button>
            )}
          </div>
          <div>
            <strong className="route-energy-total">
              {kwh(impossible ? e.unserved_energy_j : e.net_battery_j)}{" "}
              <small>kWh</small>
            </strong>
            <span>
              {impossible
                ? "Unserved energy on the planned route"
                : "Net battery energy needed"}
            </span>
          </div>
          <dl>
            <div>
              <dt>{impossible ? "Assigned distance" : "Distance remaining"}</dt>
              <dd>{value(summary.remaining_distance_m / 1000)} km</dd>
            </div>
            <div>
              <dt>Estimated duration</dt>
              <dd>
                {impossible
                  ? "Unavailable"
                  : workingDuration(summary.duration_working_s)}
              </dd>
            </div>
          </dl>
        </section>
      </div>
      <InputCoverage assumptions={e.assumptions} />
      <div className={`forecast-detail-grid ${historical ? "saved" : ""}`}>
        <section className="panel route-energy-accounts">
          <h3>Battery energy balance</h3>
          <div className="forecast-equation">
            <div>
              <span>Battery draw</span>
              <strong>
                {kwh(e.battery_draw_j)} <small>kWh</small>
              </strong>
            </div>
            <b>−</b>
            <div>
              <span>Returned to battery</span>
              <strong>
                {kwh(e.battery_recovery_j)} <small>kWh</small>
              </strong>
            </div>
            <b>=</b>
            <div>
              <span>Net battery use</span>
              <strong>
                {kwh(e.net_battery_j)} <small>kWh</small>
              </strong>
            </div>
          </div>
          <details>
            <summary>Propulsion, equipment, and braking details</summary>
            <dl className="route-energy-ledger">
              {[
                ["Propulsion demand", e.propulsion_required_j],
                ["Equipment demand", e.auxiliary_required_j],
                [
                  "Braking energy used directly by equipment",
                  Math.max(0, e.regeneration_used_j - e.battery_recovery_j),
                ],
                ["Rejected recovery", e.rejected_regeneration_j],
                ["Unserved demand", e.unserved_energy_j],
              ].map(([name, n]) => (
                <div key={String(name)}>
                  <dt>{name}</dt>
                  <dd>{kwh(Number(n))} kWh</dd>
                </div>
              ))}
            </dl>
            <p className="fine">
              Model {forecast.model_version} · Input {forecast.input_sha256}
            </p>
            <ul>
              {e.assumptions.map((a) => (
                <li key={a}>{routeReason(a)}</li>
              ))}
            </ul>
          </details>
        </section>
        {!historical && (
          <ForecastChargeTrend records={records} reserve={reserve} />
        )}
      </div>
    </>
  );
}

export function ForecastComparisons({
  data,
  openHistory,
}: {
  data:
    | (Pick<RouteEnergyView, "items" | "comparisons"> &
        Partial<Pick<RouteEnergyView, "comparison_status">>)
    | null;
  openHistory?: ForecastNavigation["openHistory"];
}) {
  const comparisons = data?.comparisons ?? [];
  const chartRows = comparisons.slice(0, 5);
  const scale = Math.max(
    360_000,
    ...chartRows.flatMap(({ payload }) => [
      Math.abs(payload.predicted_net_j),
      Math.abs(payload.observed_net_j),
    ]),
  );
  return (
    <>
      <h3>Completed prediction windows</h3>
      <p className="fine">
        Expected and observed battery use over the same working-time interval.
        Positive error means the forecast predicted more consumption.
      </p>
      {data?.comparison_status?.status === "persistent_mismatch" && (
        <p role="status" className="notice warning">
          Persistent forecast mismatch: three separate completed windows have
          errors in the same direction exceeding{" "}
          {kwh(data.comparison_status.threshold_j)} kWh. Review the vehicle and
          route assumptions.
        </p>
      )}
      {data?.comparison_status?.status === "insufficient_data" && (
        <p className="fine">
          Persistent-error check: {data.comparison_status.intervals} of 3
          separate windows available.
        </p>
      )}
      {!comparisons.length ? (
        <p>No complete comparison window is available.</p>
      ) : (
        <>
          <div className="route-energy-comparison-chart" aria-hidden="true">
            <p className="fine">
              Latest {chartRows.length} of {comparisons.length} ·{" "}
              <span className="charge-lowest">Predicted</span>
              {" / "}
              <span className="charge-measured">Observed</span>
            </p>
            {chartRows.map(({ fence, payload }) => (
              <div
                className="route-energy-comparison-row"
                key={`${fence}-${payload.until_working_s}`}
              >
                <span>
                  {value(payload.from_working_s)}–
                  {value(payload.until_working_s)} s
                </span>
                <div>
                  {[payload.predicted_net_j, payload.observed_net_j].map(
                    (joules, index) => (
                      <span
                        key={index}
                        className={
                          index === 0 ? "charge-lowest" : "charge-measured"
                        }
                      >
                        <i
                          style={{
                            left: `${joules < 0 ? 50 + (joules / scale) * 50 : 50}%`,
                            width: `${(Math.abs(joules) / scale) * 50}%`,
                          }}
                        />
                      </span>
                    ),
                  )}
                </div>
              </div>
            ))}
            <div className="route-energy-comparison-row route-energy-axis">
              <span />
              <div>
                <span>−{kwh(scale)} kWh</span>
                <span>0</span>
                <span>+{kwh(scale)} kWh</span>
              </div>
            </div>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Working interval</th>
                  <th>Predicted</th>
                  <th>Observed</th>
                  <th>Signed error</th>
                  <th>Readings</th>
                </tr>
              </thead>
              <tbody>
                {comparisons.map(({ fence, payload }) => (
                  <tr key={`${fence}-${payload.until_working_s}`}>
                    <td>
                      {value(payload.from_working_s)}–
                      {value(payload.until_working_s)} s
                    </td>
                    <td>{kwh(payload.predicted_net_j)} kWh</td>
                    <td>{kwh(payload.observed_net_j)} kWh</td>
                    <td>{kwh(payload.signed_error_j)} kWh</td>
                    <td>
                      {openHistory && (
                        <button
                          onClick={() =>
                            openHistory({
                              signals: forecastSignals,
                              from: payload.from_working_s,
                              to: payload.until_working_s,
                              cutoff: payload.from_working_s,
                              forecast: data?.items.find(
                                (r) => r.fence === fence,
                              ),
                              comparison: true,
                            })
                          }
                        >
                          Inspect readings
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
