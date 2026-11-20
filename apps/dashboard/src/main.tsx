import {
  StrictMode,
  useCallback,
  useEffect,
  useEffectEvent,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import { time, value } from "./monitoring/model.ts";
import type { Snapshot } from "./monitoring/model.ts";
import type { Tab } from "./app/presentation.ts";
import { DashboardShell } from "./app/DashboardShell.tsx";
import { Measurements } from "./features/measurements/Measurements.tsx";
import { Overview } from "./features/overview/Overview.tsx";
import { Icon } from "./ui/Icon.tsx";
import { useLive } from "./monitoring/useLive.ts";
import { MapPanel } from "./features/map/MapPanel.tsx";
import { HistoryExplorer } from "./features/history/HistoryExplorer.tsx";
import { Operations } from "./features/operations/Operations.tsx";
import { Fleet } from "./features/fleet/Fleet.tsx";
import { EnergyForecasts } from "./features/forecast/EnergyForecasts.tsx";
import { OperatorControls } from "./features/charging/OperatorControls.tsx";
import { ChargingAdvisory } from "./features/charging/ChargingAdvisory.tsx";
import {
  ChargingAlert,
  reserveWarningText,
  useChargingAlert,
  type ReviewOffer,
} from "./features/charging/ChargingAlert.tsx";
import { useChargingAdvisory } from "./features/charging/useChargingAdvisory.ts";
import { useRouteEnergy } from "./features/forecast/useRouteEnergy.ts";
import type { FleetAsset } from "./features/fleet/fleetPolling.ts";
import "./ui/theme.css";
import "./style.css";
import "./ui/loading.css";
import {
  DashboardTripControl,
  readTripControlKey,
} from "./app/DashboardTripControl.tsx";
import { Loading, Spinner } from "./ui/Loading.tsx";
import type {
  HistoryEntry,
  SavedRouteForecast,
} from "./features/forecast/forecastNavigation.ts";
import { forecastSignals } from "./features/forecast/forecastNavigation.ts";
import { chargingBayName, type TripEvent } from "./features/trip/tripReview.ts";
import { useTripReview } from "./features/trip/useTripReview.ts";
import { useTripMap } from "./features/map/useTripMap.ts";
import { remainingArrival } from "./features/trip/arrival.ts";
import type { ChargingChoice } from "./features/charging/chargingDecision.ts";
import { useNotificationSounds } from "./app/useNotificationSounds.ts";
import "./features/operations/operations-ux.css";

const tripControlKey = readTripControlKey();

function App() {
  const [token, setToken] = useState(""),
    [input, setInput] = useState(""),
    [assets, setAssets] = useState<string[]>([]);
  const [asset, setAsset] = useState(""),
    [loginError, setLoginError] = useState(""),
    [view, setView] = useState<Tab>("Overview");
  const [historyEntry, setHistoryEntry] = useState<HistoryEntry>({
    signals: [],
  });
  const [energyRecord, setEnergyRecord] = useState<SavedRouteForecast | null>(
    null,
  );
  const [mapRecord, setMapRecord] = useState<SavedRouteForecast | null>(null);
  const [mapOffer, setMapOffer] = useState<{
    run: string;
    asset: string;
    offer: ReviewOffer;
  } | null>(null);
  const [mapEvent, setMapEvent] = useState<TripEvent | null>(null);
  const [reopenedBay, setReopenedBay] = useState<{
    run: string;
    event: string;
  } | null>(null);
  const [historyKey, setHistoryKey] = useState(0);
  const [loggingIn, setLoggingIn] = useState(false);
  const [replayError, setReplayError] = useState("");
  const [vehicleControlAsset, setVehicleControlAsset] = useState("");
  const [closedSnapshot, setClosedSnapshot] = useState<Snapshot | null>(null);
  const [fleetReading, setFleetReading] = useState<FleetAsset | null>(null);
  const closeMonitoring = useCallback((final: Snapshot) => {
    setClosedSnapshot(final);
  }, []);
  function navigate(tab: Tab) {
    if (tab === "History") {
      setHistoryEntry({ signals: [] });
      setHistoryKey((k) => k + 1);
    }
    if (tab === "Energy") setEnergyRecord(null);
    if (tab === "Map") {
      setMapRecord(null);
      setMapOffer(null);
      setMapEvent(null);
    }
    setView(tab);
  }
  function openHistory(entry: HistoryEntry) {
    setHistoryEntry(entry);
    setHistoryKey((k) => k + 1);
    setView("History");
  }
  function backToForecast(record: SavedRouteForecast | null) {
    setEnergyRecord(record);
    setView("Energy");
  }
  useEffect(() => {
    setHistoryEntry({ signals: [] });
    setEnergyRecord(null);
    setMapRecord(null);
    setMapOffer(null);
    setMapEvent(null);
  }, [asset]);
  const {
    snap,
    connection: streamConnection,
    notice,
    authorized,
  } = useLive(
    view === "Fleet" || closedSnapshot ? "" : token,
    asset,
    closeMonitoring,
  );
  const displayedSnapshot = closedSnapshot ?? snap;
  const selectedFleetReading =
    fleetReading?.snap?.asset_id === asset ? fleetReading : null;
  const observed = view === "Fleet" ? selectedFleetReading?.snap : snap;
  const connection =
    view === "Fleet"
      ? (selectedFleetReading?.connection ?? "connecting")
      : streamConnection;
  const run = observed?.asset_id === asset ? observed.run_id : "";
  const ended = Boolean(
    closedSnapshot || observed?.items[0]?.source_state === "finished",
  );
  const paused = observed?.items[0]?.source_state === "paused";
  const live = Boolean(
    token && run && !closedSnapshot && connection === "live",
  );
  const canReview = live && !ended && !paused;
  const charging = useChargingAdvisory(
    asset,
    closedSnapshot?.run_id ?? run,
    token,
    live,
  );
  const sounds = useNotificationSounds();
  const notify = useEffectEvent(sounds.play);
  const fleetUnauthorized = useCallback(() => {
    setToken("");
    setAssets([]);
    setAsset("");
    setLoginError("Access expired. Sign in again to load authorized vehicles.");
  }, []);
  const forecast = useRouteEnergy(
    token,
    closedSnapshot ? "" : run,
    asset,
    live,
    fleetUnauthorized,
  );
  const currentForecast = forecast.current ?? forecast.diagnostic;
  const startupWaiting =
    forecast.data?.charging_startup_waiting ||
    charging?.status === "startup_delay";
  const risk =
    !startupWaiting &&
    currentForecast?.summary?.ledger.reserve_breached === true;
  const reserve = forecast.data?.items.find(
    (item) =>
      item.fence ===
      (forecast.data?.current_fence ?? forecast.data?.diagnostic?.fence),
  )?.reserve_soc;
  const currentOffer =
    canReview && !startupWaiting && !charging?.observed_effect
      ? charging?.estimate
      : null;
  const alert = useChargingAlert({
    signedIn: Boolean(token),
    asset,
    run: closedSnapshot?.run_id ?? run,
    live: canReview,
    minimum: risk ? (currentForecast?.summary?.minimum_soc ?? null) : null,
    threshold: reserve ?? null,
    estimate: currentOffer,
    cleared: Boolean(
      canReview &&
        forecast.current &&
        !risk &&
        !currentOffer &&
        (charging?.configured === false || charging?.status === "continue"),
    ),
  });
  const reviewRun = closedSnapshot?.run_id ?? run;
  const { events: tripEvents, record: recordTripEvent } = useTripReview(
    reviewRun,
    asset,
    Boolean(token),
  );
  const tripMap = useTripMap(
    reviewRun,
    asset,
    token,
    Boolean(token) &&
      (!closedSnapshot || view === "Map") &&
      (view === "Overview" ||
        view === "Map" ||
        view === "Operator" ||
        Boolean(alert.kind)),
  );
  const currentMapOffer =
    mapOffer?.run === reviewRun && mapOffer.asset === asset
      ? mapOffer.offer
      : null;
  const stationName = (bay: string) =>
    chargingBayName(
      bay,
      tripMap?.context?.features.find(
        ({ properties }) =>
          properties.layer === "stations" && properties.id === bay,
      )?.properties.name,
    );
  const declined = tripEvents.filter(({ kind }) => kind === "declined").at(-1);
  const approval = tripEvents.filter(({ kind }) => kind === "approved").at(-1);
  const declinedBay =
    declined &&
    (!approval || Date.parse(declined.at) > Date.parse(approval.at)) &&
    !(reopenedBay?.run === reviewRun && reopenedBay.event === declined.id)
      ? declined.bay
      : undefined;
  const warningWorking = currentForecast?.cutoff_working_s;
  const [recordedRisk, setRecordedRisk] = useState("");
  useEffect(() => {
    if (!canReview || !reviewRun) return;
    if (!risk) {
      if (forecast.current && recordedRisk) setRecordedRisk("");
      return;
    }
    const scope = `${reviewRun}:${asset}`;
    if (warningWorking === undefined || recordedRisk === scope) return;
    recordTripEvent({
      id: `reserve:${warningWorking}`,
      kind: "reserve",
      working: warningWorking,
    });
    setRecordedRisk(scope);
  }, [
    asset,
    canReview,
    forecast.current,
    recordTripEvent,
    recordedRisk,
    reviewRun,
    risk,
    warningWorking,
  ]);
  const effect = charging?.observed_effect;
  const currentReadings = observed?.items.some(
    ({ freshness }) => freshness === "fresh",
  );
  const isCharging = Boolean(
    live &&
      !ended &&
      !paused &&
      currentReadings &&
      !charging?.observed_effect_stale &&
      effect?.state === "charging",
  );
  const [approvedArrival, setApprovedArrival] = useState<{
    run: string;
    asset: string;
    bay: string;
    working: number;
  } | null>(null);
  const activeStop = effect && effect.state !== "motion_resumed";
  const chargingArrival =
    live &&
    !charging?.observed_effect_stale &&
    activeStop &&
    approvedArrival?.run === run &&
    approvedArrival.asset === asset &&
    approvedArrival.bay === effect.bay_id
      ? approvedArrival.working
      : undefined;
  const arrivalWorking = activeStop
    ? chargingArrival
    : forecast.current?.summary?.ledger.motion_feasible === true &&
        forecast.current.summary.arrival_soc !== null
      ? forecast.current.summary.arrival_working_s
      : undefined;
  const clock = Math.max(
    -1,
    ...(observed?.items.map((item) => item.observation?.sim_time_s ?? -1) ??
      []),
  );
  const [lastArrival, setLastArrival] = useState<{
    run: string;
    asset: string;
    working: number;
  } | null>(null);
  useEffect(() => {
    if (run && !ended && !paused && arrivalWorking !== undefined) {
      setLastArrival((previous) =>
        previous?.run === run &&
        previous.asset === asset &&
        previous.working === arrivalWorking
          ? previous
          : { run, asset, working: arrivalWorking },
      );
    }
  }, [arrivalWorking, asset, ended, paused, run]);
  const etaText = ended
    ? "Trip finished"
    : !live
      ? null
      : remainingArrival(
          paused && lastArrival?.run === run && lastArrival.asset === asset
            ? lastArrival.working
            : arrivalWorking,
          clock,
        );
  const arrival =
    etaText && paused
      ? `${etaText} · paused`
      : etaText && chargingArrival !== undefined && !ended
        ? `${etaText} · estimated at approval`
        : etaText;
  const alertBay = alert.current?.offer?.bay_id;
  useEffect(() => {
    if (!live || !run || !alert.kind) return;
    if (alert.kind === "reserve") notify(`${run}:${asset}:reserve`, "warning");
    else if (alertBay)
      notify(`${run}:${asset}:offer:${alertBay}`, "recommendation");
  }, [alert.kind, alertBay, asset, live, run]);
  useEffect(() => {
    if (run && isCharging) notify(`${run}:${asset}:charging`, "charging");
    if (
      run &&
      live &&
      !charging?.observed_effect_stale &&
      (effect?.state === "target_reached" || effect?.state === "motion_resumed")
    )
      notify(`${run}:${asset}:charged`, "charged");
    if (reviewRun && ended) notify(`${reviewRun}:${asset}:ended`, "arrival");
  }, [
    asset,
    charging?.observed_effect_stale,
    effect?.state,
    ended,
    isCharging,
    live,
    reviewRun,
    run,
  ]);
  useEffect(() => {
    if (!live || !effect || charging?.observed_effect_stale) return;
    recordTripEvent({
      id: `observed:${effect.bay_id}:${effect.state}`,
      kind: effect.state,
      working: effect.observed_working_s,
      bay: effect.bay_id,
    });
  }, [charging?.observed_effect_stale, effect, live, recordTripEvent]);
  useEffect(() => {
    if (view !== "Fleet" || !token) setFleetReading(null);
  }, [view, token]);
  const openFleetAsset = useCallback((name: string) => {
    setAsset(name);
    setView("Measurements");
  }, []);
  useEffect(() => {
    if (!authorized) {
      setToken("");
      setAssets([]);
      setAsset("");
      setLoginError(
        "Access expired or this asset is outside your permission. Sign in again.",
      );
    }
  }, [authorized]);
  async function connect(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (loggingIn) return;
    if (sounds.enabled) sounds.unlock();
    setLoggingIn(true);
    setLoginError("");
    try {
      const response = await fetch("/v1/catalog", {
        headers: { Authorization: `Bearer ${input}` },
        cache: "no-store",
        signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) throw Error("Credential not accepted");
      const catalog: unknown = await response.json();
      if (
        !catalog ||
        typeof catalog !== "object" ||
        !("assets" in catalog) ||
        !catalog.assets ||
        typeof catalog.assets !== "object"
      )
        throw Error("Invalid catalog");
      const names = Object.keys(catalog.assets);
      if (!names.length) throw Error("No authorized vehicles");
      setAssets(names);
      setAsset(names[0]!);
      setVehicleControlAsset(
        "vehicle_control_asset" in catalog &&
          typeof catalog.vehicle_control_asset === "string"
          ? catalog.vehicle_control_asset
          : "",
      );
      setClosedSnapshot(null);
      setToken(input);
      setInput("");
    } catch {
      setLoginError(
        "Could not connect. Check your dashboard key and local monitoring service.",
      );
    } finally {
      setLoggingIn(false);
    }
  }
  const [connectionLog, setConnectionLog] = useState<
    Record<string, { at: string; message: string }[]>
  >({});
  useEffect(() => {
    if (!snap) return;
    const key = snap.run_id + ":" + snap.asset_id;
    const message =
      connection === "live"
        ? notice.startsWith("Resynchronized")
          ? "Resynchronized with server"
          : "Dashboard connected"
        : connection === "connecting"
          ? "Connecting"
          : "Dashboard disconnected; display stale";
    setConnectionLog((old) => ({
      ...old,
      [key]: [
        ...(old[key] ?? []),
        { at: new Date().toISOString(), message },
      ].slice(-100),
    }));
  }, [connection, notice, snap?.run_id, snap?.asset_id]);
  const newest = Math.max(
    0,
    ...(displayedSnapshot?.items.map((i) => i.observation?.sim_time_s ?? 0) ??
      []),
  );
  function inspectEvent(event: TripEvent) {
    const available =
      displayedSnapshot?.items.map(({ signal_id }) => signal_id) ?? [];
    openHistory({
      signals: forecastSignals.filter((id) => available.includes(id)),
      from: Math.max(0, event.working - 30),
      to: Math.min(newest, Math.max(1, event.working + 30)),
      eventTime: event.working,
    });
  }
  function viewChargingStop(offer: ReviewOffer) {
    openFromAlert("Map");
    setMapOffer({ run: reviewRun, asset, offer });
  }
  function reviewCharging() {
    if (alert.current) alert.review();
    else navigate("Operator");
  }
  function decline(bay: string, digest: string) {
    setReopenedBay(null);
    recordTripEvent({
      id: `declined:${digest}:${crypto.randomUUID()}`,
      kind: "declined",
      working: newest,
      bay,
    });
    alert.dismiss();
  }
  function approve(command: string, choice: ChargingChoice) {
    if (choice.terms) {
      setApprovedArrival({
        run: reviewRun,
        asset,
        bay: choice.bay,
        working: choice.terms.arrivalWorkingS,
      });
    }
    recordTripEvent({
      id: `approved:${command}`,
      kind: "approved",
      working: newest,
      bay: choice.bay,
    });
    sounds.play(`${reviewRun}:${asset}:approved:${command}`, "approved");
  }
  async function openReplay() {
    const tab = window.open("about:blank", "_blank");
    if (!tab) {
      setReplayError("Allow pop-ups from DriveTwin to open replay.");
      return;
    }
    tab.opener = null;
    tab.document.body.textContent = "Opening trip replay…";
    setReplayError("");
    try {
      const response = await fetch(
        "http://127.0.0.1:18760/api/dashboard-replay",
        {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
          signal: AbortSignal.timeout(5000),
        },
      );
      if (!response.ok) throw Error("Replay access unavailable");
      const access = await response.json();
      if (
        access.url !== "http://127.0.0.1:18766" ||
        !/^[\w-]{32,128}$/.test(access.access)
      )
        throw Error("Replay access unavailable");
      tab.location.replace(
        `${access.url}/#access=${encodeURIComponent(access.access)}`,
      );
    } catch {
      tab.close();
      setReplayError(
        "Replay is available while the launcher session is open and Replay is enabled.",
      );
    }
  }
  const reviewProps = {
    openMap: viewChargingStop,
    onDecline: decline,
    onApproved: approve,
    bayName: stationName,
    declinedBay,
    reviewPaused: Boolean(paused && !ended),
    onReviewAgain: () => {
      if (declined) setReopenedBay({ run: reviewRun, event: declined.id });
    },
  };
  const alertMessage = alert.current?.warning
    ? (canReview && risk ? "" : "Earlier forecast. ") +
      reserveWarningText(alert.current.warning)
    : "Review the proposed stop and target charge.";
  const alertStatus = ended
    ? "The trip has ended."
    : paused
      ? "Trip paused for review. Resume to refresh the offer and enable approval."
      : !live
        ? "Waiting for a live update. Approval is unavailable."
        : view === "Map" &&
            currentMapOffer &&
            currentOffer &&
            currentMapOffer.bay_id !== currentOffer.bay_id
          ? "The offer changed while you were viewing the map. Review the current stop before approving."
          : currentOffer || charging?.observed_effect
            ? undefined
            : alert.kind === "recommendation"
              ? "Refreshing the offer. You can keep reviewing the last recommendation."
              : forecast.current && !risk
                ? "The latest forecast stays above reserve."
                : !forecast.fresh
                  ? "Waiting for the forecast to refresh."
                  : undefined;
  function openFromAlert(tab: Tab) {
    alert.dismiss();
    navigate(tab);
    window.scrollTo({ top: 0 });
  }
  return (
    <DashboardShell
      view={view}
      navigate={navigate}
      charging={Boolean(charging?.configured && vehicleControlAsset !== asset)}
      replay={
        token && tripControlKey && !closedSnapshot
          ? () => void openReplay()
          : undefined
      }
      toolbar={
        token ? (
          <section className="assetbar">
            <span className="asset-icon">
              <Icon name="Fleet" />
            </span>
            <label>
              Vehicle
              <select
                aria-label="Vehicle"
                value={closedSnapshot?.asset_id ?? asset}
                disabled={!!closedSnapshot}
                onChange={(e) => {
                  setAsset(e.target.value);
                }}
              >
                {assets.map((a) => (
                  <option key={a}>{a}</option>
                ))}
              </select>
            </label>
            <div className="asset-meta">
              <strong>
                {tripMap?.context?.features.find(
                  ({ properties }) => properties.layer === "route",
                )?.properties.name ?? "Live monitoring"}
              </strong>
            </div>
            {((forecast.data?.configured && live) || arrival) && (
              <span
                className="trip-arrival-chip"
                aria-label="Estimated time to arrival"
              >
                <Icon name="clock" size={17} />
                {arrival?.includes("working time")
                  ? `Arrival in ${arrival}`
                  : (arrival ?? "Updating arrival estimate…")}
              </span>
            )}
            <div className="connection" role="status">
              <span
                className={`connection-dot ${closedSnapshot ? "closed" : connection}`}
              />
              {closedSnapshot
                ? "Monitoring ended"
                : connection === "live"
                  ? "Dashboard connected"
                  : connection === "connecting"
                    ? "Connecting…"
                    : "Disconnected · reconnecting"}
            </div>
            {!closedSnapshot &&
              connection === "live" &&
              snap?.items[0]?.source_state === "paused" && (
                <span className="source-paused" role="status">
                  Trip paused
                </span>
              )}
            {tripControlKey && !closedSnapshot && !alert.kind && (
              <DashboardTripControl
                accessKey={tripControlKey}
                run={run || undefined}
                asset={asset}
              />
            )}
            {((!closedSnapshot && charging?.configured) || alert.current) && (
              <button
                type="button"
                className={
                  isCharging
                    ? "secondary charge-shortcut is-charging"
                    : alert.current ||
                        (charging?.estimate && !charging.observed_effect)
                      ? "primary charge-shortcut"
                      : "secondary charge-shortcut"
                }
                onClick={reviewCharging}
              >
                <Icon name={isCharging ? "battery" : "Energy"} />
                {isCharging
                  ? "Vehicle charging"
                  : alert.current?.offer
                    ? "Review charging stop"
                    : alert.current?.warning
                      ? "Reserve warning"
                      : "Charging status"}
              </button>
            )}
            <button
              type="button"
              className="secondary trip-sound"
              aria-label="Notification sounds"
              title={
                sounds.enabled
                  ? "Mute notification sounds"
                  : "Enable notification sounds"
              }
              aria-pressed={sounds.enabled}
              onClick={sounds.toggle}
            >
              <Icon name={sounds.enabled ? "sound-on" : "sound-off"} />
            </button>
            {!closedSnapshot &&
              risk &&
              !alert.current &&
              !isCharging &&
              !currentOffer && (
                <button
                  type="button"
                  className="secondary reserve-shortcut"
                  onClick={() =>
                    alert.current ? alert.review() : openFromAlert("Energy")
                  }
                >
                  Reserve warning
                </button>
              )}
          </section>
        ) : undefined
      }
      disconnect={
        token
          ? () => {
              setToken("");
              setAsset("");
              setAssets([]);
              setClosedSnapshot(null);
              setConnectionLog({});
            }
          : undefined
      }
    >
      {!token ? (
        <section className="panel login">
          <span className="login-icon">
            <Icon name="Fleet" />
          </span>
          <p className="eyebrow">CONNECT TO YOUR VEHICLE</p>
          <h2>Open a monitoring session</h2>
          <p>
            Paste the key from the launcher. If you enabled charging approval,
            this sign-in also unlocks the controls.
          </p>
          <form onSubmit={(e) => void connect(e)}>
            <label htmlFor="credential">Dashboard key</label>
            <input
              id="credential"
              type="password"
              autoComplete="off"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              required
            />
            <button
              className="primary"
              type="submit"
              disabled={loggingIn}
              aria-busy={loggingIn}
            >
              {loggingIn ? (
                <>
                  <Spinner /> Checking access…
                </>
              ) : (
                <>
                  Connect to monitoring <span aria-hidden="true">→</span>
                </>
              )}
            </button>
          </form>
          {loginError && (
            <p role="alert" className="error">
              {loginError}
            </p>
          )}
          <p className="fine">
            Kept in this page's memory only. Closing or refreshing the page ends
            the session.
          </p>
        </section>
      ) : view === "Fleet" && !closedSnapshot ? (
        <Fleet
          token={token}
          assets={assets}
          unauthorized={fleetUnauthorized}
          open={openFleetAsset}
          onClosed={closeMonitoring}
          monitoredAsset={asset}
          onMonitored={setFleetReading}
          monitoredWarning={
            risk
              ? "Reserve warning"
              : currentOffer
                ? "Charging stop ready"
                : undefined
          }
        />
      ) : (
        <>
          {closedSnapshot ? (
            <p className="notice" role="status">
              Trip complete. The recording was saved and live monitoring has
              ended.
            </p>
          ) : (
            notice && (
              <p
                role="status"
                className={`notice ${connection === "disconnected" ? "warning" : ""}`}
              >
                {notice}
              </p>
            )
          )}
          {!displayedSnapshot ? (
            <section className="panel empty" aria-busy="true">
              {connection === "disconnected" ? (
                "Monitoring connection unavailable. Reconnecting…"
              ) : (
                <Loading label="Loading live readings…" />
              )}
            </section>
          ) : (
            <>
              <div className="contextline">
                <span>
                  Source:{" "}
                  <strong>
                    {displayedSnapshot.items[0]?.source_state ?? "unknown"}
                  </strong>{" "}
                  · {displayedSnapshot.items.length} registered signals
                </span>
                <span>
                  Working time {value(newest)} s{" "}
                  <span className="slash">/</span> Updated{" "}
                  {time(displayedSnapshot.as_of)}
                </span>
              </div>
              {closedSnapshot &&
                !["Map", "Measurements", "Activity", "Operator"].includes(
                  view,
                ) && (
                  <section className="panel">
                    <h2>Monitoring session ended</h2>
                    <p>
                      Find the recording under Saved sessions in the launcher.
                    </p>
                    <button onClick={() => navigate("Measurements")}>
                      View final measurements
                    </button>
                  </section>
                )}
              {snap && !closedSnapshot && view === "Energy" && (
                <EnergyForecasts
                  key={`${snap.run_id}:${asset}`}
                  token={token}
                  run={snap.run_id}
                  asset={asset}
                  connected={connection === "live"}
                  unauthorized={fleetUnauthorized}
                  forecast={forecast}
                  arrival={arrival}
                  openHistory={openHistory}
                  openCharging={reviewCharging}
                  reserveCrossingWorking={charging?.reserve_crossing?.working_s}
                  openReserveMap={() => {
                    navigate("Map");
                    if (charging?.reserve_crossing)
                      setMapEvent({
                        id: "current-reserve",
                        at: new Date().toISOString(),
                        kind: "reserve",
                        working: charging.reserve_crossing.working_s,
                      });
                  }}
                  openMap={(record) => {
                    setMapOffer(null);
                    setMapEvent(null);
                    setMapRecord(record);
                    setView("Map");
                  }}
                  initialRecord={energyRecord}
                />
              )}
              {view === "Operator" && displayedSnapshot.asset_id === asset && (
                <>
                  {!alert.kind && (
                    <ChargingAdvisory
                      key={`${displayedSnapshot.run_id}:${asset}`}
                      run={displayedSnapshot.run_id}
                      token={token}
                      asset={asset}
                      connected={canReview}
                      workingTime={newest}
                      data={charging}
                      arrival={arrival}
                      {...reviewProps}
                      recentActions={tripEvents}
                      previousOffer={alert.current?.offer}
                    />
                  )}
                  {vehicleControlAsset === asset &&
                    connection === "live" &&
                    !closedSnapshot && (
                      <OperatorControls
                        key={`controls:${displayedSnapshot.run_id}:${asset}`}
                        run={displayedSnapshot.run_id}
                        asset={asset}
                        credential={token}
                      />
                    )}
                </>
              )}
              {snap && !closedSnapshot && view === "Overview" && (
                <Overview
                  snap={snap}
                  token={token}
                  connected={connection === "live"}
                  navigate={navigate}
                  forecast={forecast}
                  arrival={arrival}
                  tripMap={tripMap?.context}
                  review={currentOffer || risk ? reviewCharging : undefined}
                  reviewLabel={
                    currentOffer
                      ? "Review charging stop"
                      : "Review reserve warning"
                  }
                />
              )}
              {view === "Measurements" && (
                <Measurements
                  snap={displayedSnapshot}
                  connected={!closedSnapshot && connection === "live"}
                  openHistory={(id) => openHistory({ signals: [id] })}
                />
              )}
              {view === "Map" && (
                <MapPanel
                  key={
                    displayedSnapshot.run_id +
                    displayedSnapshot.asset_id +
                    String(mapRecord?.fence ?? "")
                  }
                  forecast={mapRecord}
                  backToForecast={() => backToForecast(mapRecord)}
                  snap={displayedSnapshot}
                  token={token}
                  connected={!closedSnapshot && connection === "live"}
                  liveMap={tripMap}
                  advisory={charging}
                  arrival={arrival}
                  isCharging={isCharging}
                  offer={currentMapOffer}
                  focusEvent={mapEvent}
                  review={currentMapOffer ? reviewCharging : undefined}
                />
              )}
              {snap && !closedSnapshot && view === "History" && (
                <HistoryExplorer
                  key={snap.run_id + snap.asset_id + historyKey}
                  snap={snap}
                  token={token}
                  entry={historyEntry}
                  tripEvents={tripEvents}
                  backToForecast={() =>
                    backToForecast(historyEntry.forecast ?? null)
                  }
                />
              )}
              {(view === "Activity" ||
                (!closedSnapshot && view === "System")) && (
                <Operations
                  key={
                    displayedSnapshot.run_id + displayedSnapshot.asset_id + view
                  }
                  snap={displayedSnapshot}
                  token={token}
                  view={view}
                  connected={connection === "live"}
                  connectionEvents={
                    connectionLog[
                      displayedSnapshot.run_id +
                        ":" +
                        displayedSnapshot.asset_id
                    ] ?? []
                  }
                  tripEvents={tripEvents}
                  inspectEvent={closedSnapshot ? undefined : inspectEvent}
                  openMap={(event) => {
                    navigate("Map");
                    setMapEvent(event);
                  }}
                />
              )}
            </>
          )}
        </>
      )}
      {replayError && (
        <p className="panel" role="alert">
          {replayError}{" "}
          <button
            type="button"
            className="secondary"
            onClick={() => setReplayError("")}
          >
            Dismiss
          </button>
        </p>
      )}
      {alert.kind && alert.current && (
        <ChargingAlert
          kind={alert.kind}
          message={alertMessage}
          status={alertStatus}
          onDismiss={alert.dismiss}
        >
          {alert.kind === "recommendation" && (
            <ChargingAdvisory
              key={`${alert.current.run}:${asset}`}
              run={alert.current.run}
              token={token}
              asset={asset}
              connected={canReview}
              workingTime={Math.max(
                0,
                ...(observed?.items.map(
                  (item) => item.observation?.sim_time_s ?? 0,
                ) ?? []),
              )}
              data={charging}
              arrival={arrival}
              {...reviewProps}
              inAlert
              previousOffer={alert.current.offer}
            />
          )}
          <footer className="charging-alert-actions">
            {tripControlKey && !ended && (
              <DashboardTripControl
                accessKey={tripControlKey}
                run={alert.current.run}
                asset={asset}
              />
            )}
            <button
              type="button"
              className="secondary"
              onClick={() => openFromAlert("Energy")}
            >
              View Energy Forecast
            </button>
            {alert.kind === "reserve" && charging?.configured && (
              <button
                type="button"
                className="primary"
                onClick={() => openFromAlert("Operator")}
              >
                Open Charging Advisor
              </button>
            )}
            {alert.kind === "reserve" && (
              <button
                type="button"
                className="secondary"
                onClick={alert.dismiss}
              >
                Close warning
              </button>
            )}
          </footer>
        </ChargingAlert>
      )}
    </DashboardShell>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
