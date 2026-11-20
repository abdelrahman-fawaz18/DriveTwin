import type { RouteEnergyRead } from "./useRouteEnergy.ts";
import { routeReason } from "./routeEnergyModel.ts";
import { Icon } from "../../ui/Icon.tsx";
import "./route-energy.css";

export function ForecastOverview({
  forecast,
  connected,
  open,
  pausedForCharge = false,
  compact = false,
  reserve,
}: {
  forecast: RouteEnergyRead;
  connected: boolean;
  open: () => void;
  pausedForCharge?: boolean;
  compact?: boolean;
  reserve?: number;
}) {
  const { current, diagnostic, data, fresh } = forecast;
  const ledger = current?.summary?.ledger;
  const state = pausedForCharge
    ? "Driving forecast paused during charging"
    : diagnostic
      ? "Route infeasible"
      : ledger?.reserve_breached
        ? "Predicted below reserve"
        : current
          ? "Predicted above reserve"
          : !connected
            ? "Monitoring disconnected"
            : data?.items.length && !fresh
              ? "Waiting for a fresh forecast"
              : routeReason(data?.reason ?? "waiting_for_forecast");
  return (
    <section
      className={`panel forecast-overview ${compact ? "compact-forecast" : ""}`}
    >
      <div className="panel-head">
        <h2>
          <Icon name="Energy" /> Energy Forecast
        </h2>
      </div>
      <p
        className={
          diagnostic || ledger?.reserve_breached
            ? "notice warning"
            : "forecast-overview-state"
        }
      >
        {state}
      </p>
      {compact && reserve !== undefined && ledger && (
        <small className="forecast-reserve">
          Reserve {(reserve * 100).toFixed(0)}%
        </small>
      )}
      {ledger && (
        <dl>
          <div className="charge-arrival">
            <dt>Predicted arrival</dt>
            <dd>
              {current?.summary?.arrival_soc == null
                ? "Unavailable"
                : `${(current.summary.arrival_soc * 100).toFixed(2)}%`}
            </dd>
          </div>
          <div className="charge-lowest">
            <dt>Lowest charge</dt>
            <dd>
              {current?.summary?.minimum_soc == null
                ? "Unavailable"
                : `${(current.summary.minimum_soc * 100).toFixed(2)}%`}
            </dd>
          </div>
        </dl>
      )}
      {compact && ledger && (
        <div className="forecast-energy-use">
          <span>Predicted energy use</span>
          <strong>
            {(ledger.net_battery_j / 3_600_000).toLocaleString("en-US", {
              maximumFractionDigits: 2,
            })}{" "}
            kWh
          </strong>
        </div>
      )}
      {current && (
        <p className="fine">
          {compact ? "Readings at" : "Updated at"} {current.cutoff_working_s}{" "}
          working seconds
        </p>
      )}
      <button onClick={open}>
        Open Energy Forecast <span aria-hidden="true">→</span>
      </button>
    </section>
  );
}
