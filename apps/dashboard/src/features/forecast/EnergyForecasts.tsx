import { Icon } from "../../ui/Icon.tsx";
import { EnergyOutcome } from "./EnergyOutcome.tsx";
import { useEffect, useState } from "react";
import { parseEnergy, reasonText } from "./energyModel.ts";
import type { EnergyView } from "./energyModel.ts";
import { time, value } from "../../monitoring/model.ts";
import { EnergyDetails } from "./EnergyDetails.tsx";
import { RouteEnergyForecasts } from "./RouteEnergyForecasts.tsx";
import type { ForecastNavigation } from "./forecastNavigation.ts";
import type { RouteEnergyRead } from "./useRouteEnergy.ts";

export function EnergyForecasts(
  props: Parameters<typeof LegacyEnergyForecasts>[0] &
    ForecastNavigation & { forecast: RouteEnergyRead; arrival?: string | null },
) {
  return (
    <RouteEnergyForecasts
      key={`${props.run}:${props.asset}`}
      {...props}
      legacy={<LegacyEnergyForecasts {...props} />}
    />
  );
}

function LegacyEnergyForecasts({
  token,
  run,
  asset,
  connected,
  unauthorized,
}: {
  token: string;
  run: string;
  asset: string;
  connected: boolean;
  unauthorized: () => void;
}) {
  const [data, setData] = useState<EnergyView | null>(null);
  const [fresh, setFresh] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let stopped = false;
    let last = 0;
    let timer: ReturnType<typeof setTimeout>;
    let request: AbortController | null = null;
    setData(null);
    setFresh(false);
    setError("");
    const watchdog = setInterval(() => {
      if (Date.now() - last > 5000) setFresh(false);
    }, 250);
    async function poll() {
      request = new AbortController();
      const timeout = setTimeout(() => request?.abort(), 4500);
      try {
        const response = await fetch(
          `/v1/energy-forecasts?asset_id=${encodeURIComponent(asset)}`,
          {
            headers: { Authorization: `Bearer ${token}` },
            cache: "no-store",
            signal: request.signal,
          },
        );
        if (response.status === 401 || response.status === 403) {
          if (!stopped) unauthorized();
          return;
        }
        if (!response.ok) throw Error();
        const result = parseEnergy(await response.json(), run, asset);
        if (!stopped) {
          setData(result);
          setFresh(true);
          setError("");
          last = Date.now();
        }
      } catch {
        if (!stopped) {
          setFresh(false);
          setError(
            "Forecast connection unavailable. Saved history may be out of date.",
          );
        }
      } finally {
        clearTimeout(timeout);
        if (!stopped) timer = setTimeout(poll, 1500);
      }
    }
    void poll();
    return () => {
      stopped = true;
      request?.abort();
      clearTimeout(timer);
      clearInterval(watchdog);
    };
  }, [token, run, asset, unauthorized]);
  const current =
    connected && fresh && data?.status === "advisory" ? data : null;
  return (
    <div className="energy-panel" aria-label="Energy forecasts">
      <section className="panel forecast-status">
        <h2>Current forecast</h2>
        <p className="fine">
          Advisory prediction from recorded observations and an assumed driving
          plan. No automatic vehicle action.
        </p>
        <p role="status" className={current ? "notice" : "notice warning"}>
          {current ? (
            "Advisory forecast available"
          ) : !data && !error && connected ? (
            <Loading label="Loading forecasts…" />
          ) : !connected || !fresh ? (
            "Forecast stale or disconnected — current prediction hidden"
          ) : (
            `Forecast unavailable — ${data?.reason_codes.map(reasonText).join(", ") ?? "waiting for forecast data"}`
          )}
        </p>
        {error && <p role="alert">{error}</p>}
        {!current &&
          fresh &&
          data?.reason_codes.includes("forecast_not_configured") && (
            <div className="forecast-empty">
              <Icon name="Energy" size={32} />
              <div>
                <h3>No forecast producer in this run</h3>
                <p>
                  Live readings and history are available. Route energy and
                  station estimates appear when the energy service writes a
                  forecast for this vehicle.
                </p>
              </div>
            </div>
          )}
      </section>
      {current?.values && current.uncertainty && (
        <div className="metrics-row three forecast-metrics">
          <article className="metric-card gradient pink">
            <Icon name="Energy" />
            <span>Estimated remaining route energy</span>
            <strong data-testid="energy-estimate">
              {value(current.values.net_mission_energy_kwh)} kWh
            </strong>
            <small>
              Scenario range {value(current.uncertainty.minimum_energy_kwh)}–
              {value(current.uncertainty.maximum_energy_kwh)} kWh
            </small>
          </article>
          <article className="metric-card gradient violet">
            <Icon name="battery" />
            <span>Estimated final battery charge</span>
            <strong>{value(current.values.projected_final_soc * 100)}%</strong>
            <small>
              Scenario range{" "}
              {value(current.uncertainty.minimum_final_soc * 100)}–
              {value(current.uncertainty.maximum_final_soc * 100)}%
            </small>
          </article>
          <article className="metric-card gradient charcoal">
            <Icon name="shield" />
            <span>Declared 10% reserve</span>
            <strong>
              {current.values.reserve_feasible
                ? "Maintained in assumed plan"
                : "Predicted shortfall"}
            </strong>
            <small>Scenario ranges have no probability guarantee.</small>
          </article>
        </div>
      )}
      {current && (
        <section className="panel forecast-body">
          <EnergyOutcome data={current} />
          <EnergyDetails data={current} />
        </section>
      )}
      {!current && data?.input_age_s != null && (
        <p className="fine">
          Latest reading age: {value(data.input_age_s)} seconds. Current
          prediction details are hidden.
        </p>
      )}
      <section className="panel forecast-history">
        <h3>Saved forecast history</h3>
        <p className="fine">
          Historical records stay unchanged. Their values are not a current
          driving recommendation. The evaluated model covers the known route at
          120 working seconds; other cutoffs are unavailable.
        </p>
        {!data?.items.length ? (
          <p>No saved forecasts for this vehicle.</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Saved (UTC)</th>
                  <th>Working time</th>
                  <th>Outcome</th>
                  <th>Energy / final charge</th>
                  <th>Model and traceability</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map(({ sequence, created_at, payload: p }) => (
                  <tr key={sequence}>
                    <td>{time(created_at)}</td>
                    <td>
                      {p.source_clock
                        ? `${value(p.source_clock.sim_time_s)} s`
                        : "Unavailable"}
                    </td>
                    <td>
                      {p.status}
                      <br />
                      {p.reason_codes.map(reasonText).join(", ")}
                    </td>
                    <td>
                      {p.values
                        ? `${value(p.values.net_mission_energy_kwh)} kWh / ${value(p.values.projected_final_soc * 100)}%`
                        : "Unavailable"}
                    </td>
                    <td>
                      <details>
                        <summary>{p.model_version}</summary>
                        <p>
                          {p.observation_references.length} original observation
                          references
                          <br />
                          Numerical model: {p.numerical_model_version}
                          <br />
                          Release: {p.release_sha256}
                          <br />
                          Record: {p.record_id}
                          <br />
                          Uncertainty: {p.uncertainty?.method ?? "unavailable"}
                          <br />
                          Automatic dispatch: disabled for this advisory record
                        </p>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.next && (
              <p className="fine">
                Showing the latest {data.items.length} records. Older records
                remain available through the scoped history interface.
              </p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
import { Loading } from "../../ui/Loading.tsx";
