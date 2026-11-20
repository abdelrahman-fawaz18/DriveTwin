import { Icon } from "../../ui/Icon.tsx";
import {
  chargeValues,
  percent,
  workingDuration,
} from "./RouteEnergyDetails.tsx";
import { ForecastChargeTrend } from "./ForecastChargeTrend.tsx";
import { routeReason, type RouteForecast } from "./routeEnergyModel.ts";
import type { SavedRouteForecast } from "./forecastNavigation.ts";

const number = (value: number) =>
  value.toLocaleString("en-US", { maximumFractionDigits: 2 });
const kwh = (joules: number) => number(joules / 3_600_000);

/** The same immutable forecast ledger, presented for the static recording. */
export function RecordedForecastSummary({
  forecast,
  reserve,
  records,
  historical,
  openMap,
}: {
  forecast: RouteForecast;
  reserve: number;
  records: SavedRouteForecast[];
  historical: boolean;
  openMap: () => void;
}) {
  const summary = forecast.summary;
  if (!summary)
    return (
      <section className="panel recorded-empty-state">
        <Icon name="Energy" />
        <h2>Forecast unavailable</h2>
        <p>{routeReason(forecast.reason)}</p>
      </section>
    );
  const charges = chargeValues(summary),
    ledger = summary.ledger,
    impossible = forecast.status === "infeasible";
  return (
    <>
      <div className="route-energy-summary recorded-energy-summary">
        <section className="panel route-energy-charge">
          <div className="panel-head">
            <h2>
              <Icon name="battery" size={18} /> Battery charge
            </h2>
            <span className="fine">
              {historical ? "Saved forecast" : "Remaining trip estimate"}
            </span>
          </div>
          <dl className="forecast-charge-values">
            {(
              [
                ["measured", "Measured at forecast"],
                ["lowest", "Lowest predicted"],
                ["arrival", "Predicted arrival"],
              ] as const
            ).map(([key, label]) => (
              <div className={`charge-${key}`} key={key}>
                <dt>{label}</dt>
                <dd>{percent(charges[key])}</dd>
              </div>
            ))}
          </dl>
          <div
            className="recorded-charge-scale"
            role="img"
            aria-label={`Measured charge ${percent(charges.measured)}; reserve ${percent(reserve)}`}
          >
            <div>
              <span style={{ width: `${(charges.measured ?? 0) * 100}%` }} />
              <i style={{ left: `${reserve * 100}%` }} />
            </div>
            <small>0%</small>
            <small
              className="recorded-reserve-mark"
              style={{ left: `${reserve * 100}%` }}
            >
              Reserve {number(reserve * 100)}%
            </small>
            <small>100%</small>
          </div>
        </section>
        <section className="panel gradient pink route-energy-remaining">
          <div className="panel-head">
            <h2>Remaining route</h2>
            <button onClick={openMap}>
              View route <Icon name="arrow" size={15} />
            </button>
          </div>
          <div>
            <strong className="route-energy-total">
              {kwh(
                impossible ? ledger.unserved_energy_j : ledger.net_battery_j,
              )}
              <small> kWh</small>
            </strong>
            <span>
              {impossible
                ? "Unserved energy on the planned route"
                : "Net battery energy needed"}
            </span>
          </div>
          <dl>
            <div>
              <dt>Distance</dt>
              <dd>{number(summary.remaining_distance_m / 1000)} km</dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd>
                {impossible
                  ? "Unavailable"
                  : workingDuration(summary.duration_working_s)}
              </dd>
            </div>
          </dl>
        </section>
      </div>
      <section className="panel route-energy-accounts recorded-energy-balance">
        <div className="panel-head">
          <h2>Battery energy balance</h2>
          <span className="fine">At the selected forecast cutoff</span>
        </div>
        <div className="forecast-equation">
          <div>
            <span>Drawn from battery</span>
            <strong>
              {kwh(ledger.battery_draw_j)} <small>kWh</small>
            </strong>
          </div>
          <b>−</b>
          <div>
            <span>Recovered to battery</span>
            <strong>
              {kwh(ledger.battery_recovery_j)} <small>kWh</small>
            </strong>
          </div>
          <b>=</b>
          <div>
            <span>Net battery use</span>
            <strong>
              {kwh(ledger.net_battery_j)} <small>kWh</small>
            </strong>
          </div>
        </div>
        <details className="recorded-details">
          <summary>Propulsion, equipment & input assumptions</summary>
          <dl className="route-energy-ledger">
            {[
              ["Propulsion demand", ledger.propulsion_required_j],
              ["Equipment demand", ledger.auxiliary_required_j],
              [
                "Braking energy used directly by equipment",
                Math.max(
                  0,
                  ledger.regeneration_used_j - ledger.battery_recovery_j,
                ),
              ],
              ["Rejected recovery", ledger.rejected_regeneration_j],
              ["Unserved demand", ledger.unserved_energy_j],
            ].map(([label, value]) => (
              <div key={String(label)}>
                <dt>{label}</dt>
                <dd>{kwh(Number(value))} kWh</dd>
              </div>
            ))}
          </dl>
          <ul>
            {ledger.assumptions.map((a) => (
              <li key={a}>{routeReason(a)}</li>
            ))}
          </ul>
        </details>
      </section>
      {!historical &&
        records.filter((r) => r.payload.status === "available").length > 1 && (
          <ForecastChargeTrend records={records} reserve={reserve} />
        )}
    </>
  );
}
