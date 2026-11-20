import { Loading, Spinner } from "../../ui/Loading.tsx";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Icon } from "../../ui/Icon.tsx";
import { SectionHeading } from "../../ui/SectionHeading.tsx";
import { value } from "../../monitoring/model.ts";
import {
  parseRouteEnergy,
  routeReason,
  type RouteEnergyView,
} from "./routeEnergyModel.ts";
import "./route-energy.css";
import type { RouteEnergyRead } from "./useRouteEnergy.ts";
import { useChargingAdvisory } from "../charging/useChargingAdvisory.ts";
import {
  ForecastComparisons,
  ForecastDetails,
  chargeSeries,
  chargeValues,
  percent,
  kwh,
  workingDuration,
} from "./RouteEnergyDetails.tsx";
export { percent } from "./RouteEnergyDetails.tsx";
import { ObservedChargingStop } from "../charging/ObservedChargingStop.tsx";
import { chargingBayName } from "../trip/tripReview.ts";
import {
  forecastSignals,
  type ForecastNavigation,
} from "./forecastNavigation.ts";

type SavedForecast = RouteEnergyView["items"][number];
type ForecastSection = "saved" | "comparison" | "original";

function ForecastCharging({
  asset,
  run,
  token,
  connected,
  reserveRisk,
  forecastCutoff,
  openCharging,
}: {
  asset: string;
  run: string;
  token: string;
  connected: boolean;
  reserveRisk: boolean;
  forecastCutoff?: number;
  openCharging: () => void;
}) {
  const advisory = useChargingAdvisory(asset, run, token, connected);
  const effect =
    connected && !advisory?.observed_effect_stale
      ? advisory?.observed_effect
      : null;
  const currentAfterCharge =
    effect?.state === "motion_resumed" &&
    forecastCutoff !== undefined &&
    forecastCutoff > effect.observed_working_s;
  const observed = effect && !currentAfterCharge ? effect : null;
  const estimate = connected && reserveRisk ? advisory?.estimate : null;
  if (observed) {
    const forecastNote = {
      approaching: undefined,
      waiting_for_charger: undefined,
      charging: "Driving forecasts resume after the bus departs.",
      target_reached: "Driving forecasts resume when the bus moves.",
      motion_resumed:
        "Waiting for a forecast based on the new battery reading.",
    }[observed.state];
    return (
      <ObservedChargingStop
        effect={observed}
        openAdvisor={openCharging}
        forecastNote={forecastNote}
      />
    );
  }
  if (!reserveRisk) return null;
  return (
    <section className="panel" aria-label="Charging and the driving forecast">
      <h3>Charging options</h3>
      {estimate ? (
        <p>
          Proposed stop at {chargingBayName(estimate.bay_id)}. Predicted
          departure target {percent(estimate.target_soc)} and estimated transfer
          time {workingDuration(estimate.charging_working_s)}.
        </p>
      ) : (
        <p>Review compatible stops and their effect on reserve.</p>
      )}
      <button type="button" onClick={openCharging}>
        Open Charging Advisor
      </button>
    </section>
  );
}

const sections: ReadonlyArray<[ForecastSection, string]> = [
  ["saved", "Saved forecasts"],
  ["comparison", "Forecast vs. observed"],
  ["original", "Original model records"],
];

function SavedForecasts({
  data,
  selected,
  select,
}: {
  data: RouteEnergyView | null;
  selected: SavedForecast | null;
  select: (record: SavedForecast) => void;
}) {
  if (!data?.items.length)
    return <p>No route forecasts saved for this vehicle.</p>;
  const selectedFence = selected?.fence ?? data.current_fence;
  return (
    <>
      <div className="table-scroll">
        <table className="route-energy-saved-table">
          <thead>
            <tr>
              <th>Working cutoff</th>
              <th>Update reason</th>
              {chargeSeries.map(({ key, label }) => (
                <th key={key} className={`charge-${key}`}>
                  {label}
                </th>
              ))}
              <th>Net energy</th>
              <th>Result</th>
              <th>Model</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((record) => {
              const forecast = record.payload;
              const charges = chargeValues(forecast.summary);
              return (
                <tr
                  key={record.fence}
                  data-selected={record.fence === selectedFence}
                >
                  <td>
                    <button
                      type="button"
                      className="route-energy-record"
                      aria-label={`View saved forecast at ${value(forecast.cutoff_working_s)} working seconds, record ${record.fence}`}
                      aria-pressed={selected?.fence === record.fence}
                      onClick={() => select(record)}
                    >
                      {value(forecast.cutoff_working_s)} s
                    </button>
                  </td>
                  <td>{routeReason(record.trigger)}</td>
                  {chargeSeries.map(({ key }) => (
                    <td
                      key={key}
                      className={`charge-${key} route-energy-number`}
                    >
                      {percent(charges[key])}
                    </td>
                  ))}
                  <td>
                    {forecast.summary
                      ? `${kwh(forecast.summary.ledger.net_battery_j)} kWh`
                      : "—"}
                  </td>
                  <td>{routeReason(forecast.reason ?? forecast.status)}</td>
                  <td>
                    <details>
                      <summary>{forecast.model_version}</summary>
                      <p className="fine">{forecast.input_sha256}</p>
                    </details>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {data.next && (
        <p className="fine">
          Showing the latest {data.items.length} records. Older results remain
          available through the scoped history interface.
        </p>
      )}
    </>
  );
}

export function RouteEnergyForecasts({
  token,
  run,
  asset,
  connected,
  unauthorized,
  legacy,
  openHistory,
  openMap,
  initialRecord,
  openCharging,
  reserveCrossingWorking,
  openReserveMap,
  forecast: forecastRead,
  arrival,
}: {
  token: string;
  run: string;
  asset: string;
  connected: boolean;
  unauthorized: () => void;
  legacy: ReactNode;
  forecast: RouteEnergyRead;
  arrival?: string | null;
} & ForecastNavigation) {
  const { data, fresh, current, diagnostic, unsupported, error, last, now } =
    forecastRead;
  const [section, setSection] = useState<ForecastSection>("saved");
  const [selected, setSelected] = useState<SavedForecast | null>(
    initialRecord ?? null,
  );
  const [older, setOlder] = useState<SavedForecast[]>([]);
  const [pageCursor, setPageCursor] = useState<number | null | undefined>(
    undefined,
  );
  const [pageError, setPageError] = useState("");
  const [loadingOlder, setLoadingOlder] = useState(false);
  const pageRequest = useRef<AbortController | null>(null);
  useEffect(() => () => pageRequest.current?.abort(), []);
  const forecastSelectId = useId();
  const forecastSelect = useRef<HTMLSelectElement>(null);
  async function loadOlder() {
    const before = pageCursor === undefined ? data?.next : pageCursor;
    if (!before || loadingOlder) return;
    const own = new AbortController();
    pageRequest.current = own;
    setLoadingOlder(true);
    setPageError("");
    const timeout = setTimeout(() => own.abort(), 8000);
    try {
      const r = await fetch(
        `/v2/energy-forecasts?asset_id=${encodeURIComponent(asset)}&before=${before}`,
        {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: own.signal,
        },
      );
      if (r.status === 401 || r.status === 403) {
        unauthorized();
        return;
      }
      if (!r.ok) throw Error();
      const page = parseRouteEnergy(await r.json(), run, asset);
      if (
        page.items.some((item) => item.fence >= before) ||
        (page.next !== null && page.next >= before)
      )
        throw Error();
      if (!own.signal.aborted) {
        setOlder((old) => [
          ...new Map([...old, ...page.items].map((r) => [r.fence, r])).values(),
        ]);
        setPageCursor(page.next);
      }
    } catch {
      if (pageRequest.current === own)
        setPageError("Older forecasts could not be loaded. Retry to continue.");
    } finally {
      clearTimeout(timeout);
      if (pageRequest.current === own) setLoadingOlder(false);
    }
  }
  if (unsupported || data?.configured === false)
    return (
      <>
        <section className="panel" aria-label="Forecast model availability">
          <h2>Route-aware forecast unavailable</h2>
          <p>
            This session does not provide the route-aware Energy Forecast.
            Original model records are shown below for historical compatibility.
            Their known-route assumptions and evaluation scope apply.
          </p>
        </section>
        {legacy}
      </>
    );

  const active = current ?? diagnostic;
  const activeFence = data?.current_fence ?? data?.diagnostic?.fence;
  const displayedRecord =
    selected ??
    (active ? data?.items.find((r) => r.fence === activeFence) : null);
  const forecast = selected?.payload ?? active;
  const reserve = displayedRecord?.reserve_soc ?? null;
  const allRecords = [
    ...new Map(
      [...(data?.items ?? []), ...older].map((r) => [r.fence, r]),
    ).values(),
  ].sort((a, b) => b.fence - a.fence);
  const tableData = data ? { ...data, items: allRecords, next: null } : null;
  const options = allRecords;
  const selectionOptions =
    selected && !options.some(({ fence }) => fence === selected.fence)
      ? [...options, selected]
      : options;

  return (
    <div
      className="energy-panel route-energy"
      aria-label="Route-aware energy forecasts"
    >
      <section className="panel route-energy-header">
        <div className="route-energy-header-top">
          <div>
            <SectionHeading
              eyebrow="REMAINING TRIP"
              title={selected ? "Saved forecast" : "Route-aware forecast"}
              icon="Energy"
              tone="raspberry"
            />
            <p
              role="status"
              className={`route-energy-status ${!selected && current && !current.summary?.ledger.reserve_breached ? "is-current" : ""}`}
            >
              {selected
                ? "Saved forecast"
                : diagnostic
                  ? "Route infeasible"
                  : current
                    ? current.summary?.ledger.reserve_breached
                      ? "Predicted below reserve"
                      : "Current forecast available · Above reserve"
                    : !connected
                      ? "Monitoring disconnected"
                      : data?.items.length && !fresh
                        ? "Waiting for a fresh forecast"
                        : !data?.items.length &&
                            (!data?.reason ||
                              data.reason === "forecast_pending")
                          ? "Waiting for the first forecast"
                          : routeReason(data?.reason ?? "forecast_pending")}
            </p>
          </div>
          {selected && (
            <button className="primary" onClick={() => setSelected(null)}>
              Back to current
            </button>
          )}
          <div className="route-energy-picker">
            <label htmlFor={forecastSelectId}>Forecast</label>
            <select
              id={forecastSelectId}
              ref={forecastSelect}
              value={selected?.fence ?? "current"}
              onChange={(event) =>
                setSelected(
                  selectionOptions.find(
                    ({ fence }) => String(fence) === event.target.value,
                  ) ?? null,
                )
              }
            >
              <option value="current">Current forecast</option>
              {selectionOptions.map((record) => (
                <option key={record.fence} value={record.fence}>
                  {value(record.payload.cutoff_working_s)} working seconds · #
                  {record.fence}
                </option>
              ))}
            </select>
          </div>
        </div>
        {error && <p role="alert">{error}</p>}
        {!data && !error && connected && <Loading label="Loading forecasts…" />}
        {!selected && (
          <p className="trip-arrival-detail">
            <Icon name="clock" size={18} />
            <span>Estimated time to arrival</span>{" "}
            <strong>{arrival ?? "Unavailable"}</strong>
          </p>
        )}
        {forecast && (
          <div className="forecast-header-body">
            <dl className="forecast-facts">
              <div>
                <dt>Forecast cutoff</dt>
                <dd>{value(forecast.cutoff_working_s)} working s</dd>
              </div>
              {current && !selected && (
                <div>
                  <dt>Input age</dt>
                  <dd>
                    {value(
                      Math.max(
                        0,
                        Math.round(
                          (data?.real_life_age_s ?? 0) + (now - last) / 1000,
                        ),
                      ),
                    )}{" "}
                    real-life s ago
                  </dd>
                </div>
              )}
            </dl>
            {openHistory && displayedRecord && (
              <button
                className="secondary"
                onClick={() =>
                  openHistory({
                    signals: forecastSignals,
                    from: Math.max(0, forecast.cutoff_working_s - 60),
                    to: forecast.cutoff_working_s,
                    cutoff: forecast.cutoff_working_s,
                    forecast: displayedRecord,
                  })
                }
              >
                Inspect battery readings
              </button>
            )}
          </div>
        )}
        {!selected && fresh && data?.lifecycle?.phase === "calculating" && (
          <Loading label="Calculating next forecast…" />
        )}
        {(forecast || data?.lifecycle) && (
          <details className="forecast-header-details">
            <summary>Forecast information</summary>
            <dl>
              {displayedRecord && (
                <div>
                  <dt>Update trigger</dt>
                  <dd>{routeReason(displayedRecord.trigger)}</dd>
                </div>
              )}
              {forecast && (
                <div>
                  <dt>Model</dt>
                  <dd>{forecast.model_version}</dd>
                </div>
              )}
              {displayedRecord?.route_revision && (
                <div>
                  <dt>Route revision</dt>
                  <dd>{displayedRecord.route_revision.slice(0, 8)}</dd>
                </div>
              )}
              {current && !selected && (
                <div>
                  <dt>Working age</dt>
                  <dd>{value(data?.working_age_s)} s</dd>
                </div>
              )}
              {!selected && data?.lifecycle && (
                <div>
                  <dt>Calculation</dt>
                  <dd>
                    {routeReason(data.lifecycle.phase)} ·{" "}
                    {routeReason(data.lifecycle.reason)}
                  </dd>
                </div>
              )}
            </dl>
          </details>
        )}
      </section>
      {!selected &&
        data?.charging_advisor_configured === true &&
        !data.charging_startup_waiting &&
        openCharging && (
          <ForecastCharging
            asset={asset}
            run={run}
            token={token}
            connected={connected && fresh}
            reserveRisk={forecast?.summary?.ledger.reserve_breached === true}
            forecastCutoff={current?.cutoff_working_s}
            openCharging={openCharging}
          />
        )}
      {forecast && (
        <ForecastDetails
          reserveCrossingWorking={selected ? undefined : reserveCrossingWorking}
          openReserveMap={selected ? undefined : openReserveMap}
          forecast={forecast}
          reserve={reserve}
          historical={!!selected}
          record={displayedRecord}
          records={allRecords}
          openMap={openMap}
        />
      )}
      <section className="panel route-energy-records">
        <div
          className="route-energy-section-nav"
          role="group"
          aria-label="Forecast records"
        >
          {sections.map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={section === key}
              onClick={() => setSection(key)}
            >
              {label}
            </button>
          ))}
        </div>
        {section === "saved" && (
          <SavedForecasts
            data={tableData}
            selected={selected}
            select={(record) => {
              setSelected(record);
              forecastSelect.current?.focus();
            }}
          />
        )}
        {section === "saved" && (
          <>
            <p role="alert">{pageError}</p>
            {(pageCursor === undefined ? data?.next : pageCursor) && (
              <button disabled={loadingOlder} onClick={() => void loadOlder()}>
                {loadingOlder ? (
                  <>
                    <Spinner /> Loading older forecasts…
                  </>
                ) : (
                  "Load older forecasts"
                )}
              </button>
            )}
          </>
        )}
        {section === "comparison" && (
          <ForecastComparisons data={data} openHistory={openHistory} />
        )}
        {section === "original" && legacy}
      </section>
    </div>
  );
}
