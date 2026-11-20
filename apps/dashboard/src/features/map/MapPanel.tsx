import { MapLayers, MapLegend } from "./MapLayers.tsx";
import { useEffect, useMemo, useRef, useState } from "react";
import { Loading } from "../../ui/Loading.tsx";
import * as L from "leaflet";
import { Icon } from "../../ui/Icon.tsx";
import { SectionHeading } from "../../ui/SectionHeading.tsx";
import { useMapScene } from "./useMapScene.ts";
import {
  chargingMarkers,
  currentPosition,
  passengerStopLabel,
} from "./mapModel.ts";
import type { MapContext } from "./mapModel.ts";
import type { Snapshot } from "../../monitoring/model.ts";
import type { SavedRouteForecast } from "../forecast/forecastNavigation.ts";
import { time, value } from "../../monitoring/model.ts";
import type { Advisory } from "../charging/useChargingAdvisory.ts";
import type { ReviewOffer } from "../charging/ChargingAlert.tsx";
import { useTripMap } from "./useTripMap.ts";
import {
  useObservedTrajectory,
  type TrajectoryWindow,
} from "./useObservedTrajectory.ts";
import { chargingBayName, type TripEvent } from "../trip/tripReview.ts";

export function MapPanel({
  snap,
  token,
  connected,
  forecast,
  backToForecast,
  liveMap,
  advisory,
  arrival,
  isCharging = false,
  offer,
  review,
  focusEvent,
}: {
  snap: Snapshot;
  token: string;
  connected: boolean;
  forecast?: SavedRouteForecast | null;
  backToForecast?: () => void;
  liveMap?: { context: MapContext | null; error: boolean } | null;
  advisory?: Advisory | null;
  arrival?: string | null;
  isCharging?: boolean;
  offer?: ReviewOffer | null;
  review?: () => void;
  focusEvent?: TripEvent | null;
}) {
  const savedMap = useTripMap(
    snap.run_id,
    snap.asset_id,
    token,
    Boolean(forecast),
    forecast,
  );
  const loadedMap = forecast ? savedMap : liveMap;
  const context = loadedMap?.context ?? null;
  const contextError = loadedMap?.error
    ? forecast
      ? "The saved forecast route is unavailable."
      : "No published mission map is available for this vehicle."
    : "";
  const [trajectoryWindow, setTrajectoryWindow] =
    useState<TrajectoryWindow>("full");
  const {
    points,
    error,
    loading: loadingTrack,
    maxGap,
  } = useObservedTrajectory(snap, token, trajectoryWindow, forecast);
  const [layers, setLayers] = useState([
      "roads",
      "labels",
      "trajectory",
      "route",
      "stops",
      "stations",
      "endpoints",
    ]),
    [follow, setFollow] = useState(false);
  const position = forecast ? null : currentPosition(snap, connected),
    last = points.filter((p) => p.coordinates).at(-1);
  const progress = connected && !forecast ? snap.trip_progress : undefined;
  const chargingAvailable = connected && !forecast && !snap.monitoring_closed;
  const estimate =
    chargingAvailable && advisory?.configured ? advisory.estimate : null;
  const approved = chargingAvailable
    ? advisory?.observed_effect?.bay_id
    : undefined;
  const reserve = chargingAvailable ? advisory?.reserve_crossing : undefined;
  const reserveLon = reserve?.coordinates?.[0];
  const reserveLat = reserve?.coordinates?.[1];
  const reserveTime = reserve?.working_s;
  const preferredBay = estimate?.fallback_only ? undefined : estimate?.bay_id;
  const fallbackBay = estimate?.fallback_only
    ? estimate.bay_id
    : (estimate?.fallback_bay_id ?? undefined);
  const charging = useMemo(
    () =>
      chargingMarkers(
        context,
        {
          preferred: preferredBay,
          fallback: fallbackBay,
          approved,
          review: offer?.bay_id,
        },
        reserveLon !== undefined &&
          reserveLat !== undefined &&
          reserveTime !== undefined
          ? { coordinates: [reserveLon, reserveLat], working_s: reserveTime }
          : undefined,
      ),
    [
      context,
      preferredBay,
      fallbackBay,
      approved,
      offer?.bay_id,
      reserveLon,
      reserveLat,
      reserveTime,
    ],
  );
  const { host, map } = useMapScene(
    context,
    points,
    maxGap,
    position,
    layers,
    follow,
    charging,
    chargingAvailable && isCharging,
  );
  const approvedDetour =
    approved &&
    context?.features.find(
      ({ properties }) =>
        properties.layer === "detour" && properties.id === approved,
    );
  const focusBay = offer?.bay_id ?? focusEvent?.bay;
  const focusStation =
    !forecast && focusBay
      ? context?.features.find(
          ({ properties }) =>
            properties.layer === "stations" && properties.id === focusBay,
        )
      : undefined;
  const focusKey = focusBay ?? focusEvent?.id;
  const focusPoint =
    focusStation?.geometry.type === "Point"
      ? focusStation.geometry.coordinates
      : focusEvent?.id === "current-reserve" &&
          reserve?.working_s === focusEvent.working
        ? reserve.coordinates
        : null;
  const focused = useRef<{
    map: L.Map;
    key: string;
    framedBus: boolean;
  } | null>(null);
  useEffect(() => {
    const own = map.current;
    if (!focusKey || !focusPoint || !own) return;
    const fix = position?.coordinates ?? last?.coordinates;
    if (
      focused.current?.map === own &&
      focused.current.key === focusKey &&
      (focused.current.framedBus || !fix)
    )
      return;
    const station: L.LatLngTuple = [focusPoint[1]!, focusPoint[0]!];
    if (fix) {
      own.fitBounds([station, [fix[1], fix[0]]], {
        padding: [55, 55],
        maxZoom: 17,
      });
    } else own.setView(station, 16);
    focused.current = { map: own, key: focusKey, framedBus: Boolean(fix) };
  }, [focusKey, last, focusPoint, map, position]);

  const fit = () => {
    if (!context || !map.current) return;
    const route = L.geoJSON(
      context.features.filter((f) => f.properties.layer === "route"),
    ).getBounds();
    map.current.fitBounds(
      route.isValid()
        ? route
        : L.latLngBounds([
            [context.bbox[1], context.bbox[0]],
            [context.bbox[3], context.bbox[2]],
          ]),
      { padding: [35, 35], maxZoom: 16 },
    );
  };
  return (
    <>
      {forecast && (
        <section className="panel forecast-context">
          <div>
            <strong>
              Saved forecast route · {forecast.payload.cutoff_working_s} working
              seconds
            </strong>
            <p className="fine">
              Route {forecast.route_revision?.slice(0, 8)} · Original cutoff and
              remaining geometry
            </p>
          </div>
          <button onClick={backToForecast}>Back to Energy Forecast</button>
        </section>
      )}
      <div className="map-layout connected-map">
        <section className="map-card">
          {!context && snap.monitoring_closed ? (
            <p className="empty">
              Open the saved recording to browse the trip map.
            </p>
          ) : (
            !context &&
            !contextError && (
              <div className="map-loading">
                <Loading label="Loading route map…" />
              </div>
            )
          )}
          {contextError ? (
            <p className="empty" role="status">
              {contextError}
            </p>
          ) : (
            <div
              ref={host}
              className="vehicle-map"
              role="region"
              aria-label="Vehicle map with planned route and observed trajectory"
            />
          )}
          <div className="map-tools">
            <button
              disabled={!context}
              onClick={fit}
              title="Fit map"
              aria-label="Fit map"
            >
              <Icon name="locate" />
            </button>
            <button
              title="Center on vehicle"
              aria-label="Center on vehicle"
              disabled={!position}
              onClick={() =>
                position &&
                map.current?.setView(
                  [position.coordinates[1], position.coordinates[0]],
                  17,
                )
              }
            >
              <Icon name="Map" />
            </button>
          </div>
          <MapLegend
            items={[
              { tone: "observed", label: "Observed trajectory" },
              { tone: "planned", label: "Planned route" },
              { tone: "stop", label: "Passenger stop" },
              { tone: "departure", label: "Departure" },
              { tone: "destination", label: "Destination" },
              { tone: "charge", label: "Charging bay", icon: "ϟ" },
              ...(approved
                ? [
                    {
                      tone: "approved",
                      label: "Approved charging bay",
                      icon: "ϟ",
                    },
                  ]
                : []),
              ...(approvedDetour
                ? [{ tone: "detour", label: "Charging detour" }]
                : []),
              ...(charging.some(({ kind }) => kind === "preferred")
                ? [{ tone: "recommended", label: "Recommended bay", icon: "ϟ" }]
                : []),
              ...(charging.some(({ kind }) => kind === "fallback")
                ? [{ tone: "fallback", label: "Alternate bay", icon: "◇" }]
                : []),
              ...(charging.some(({ kind }) => kind === "reserve")
                ? [
                    {
                      tone: "reserve",
                      label: "Predicted reserve crossing",
                      icon: "!",
                    },
                  ]
                : []),
            ]}
          />
          {approvedDetour && (
            <button
              className="secondary map-fit-action"
              title="Frame the approved detour from the turn to the rejoin point"
              onClick={() => {
                const bounds = L.geoJSON(approvedDetour).getBounds();
                if (bounds.isValid()) {
                  setFollow(false);
                  setLayers((visible) =>
                    visible.includes("route") ? visible : [...visible, "route"],
                  );
                  map.current?.fitBounds(bounds, {
                    padding: [50, 50],
                    maxZoom: 17,
                  });
                }
              }}
            >
              Fit charging detour
            </button>
          )}
        </section>
        <aside className="stack">
          {focusEvent && !offer && !forecast && (
            <section className="panel map-charging-context">
              <h2>
                {focusBay
                  ? chargingBayName(focusBay, focusStation?.properties.name)
                  : "Predicted reserve crossing"}
              </h2>
              <p>{value(focusEvent.working)} working s</p>
              {loadedMap && !focusPoint && (
                <p className="fine">A matching location is unavailable.</p>
              )}
            </section>
          )}
          {offer && !forecast && (
            <section
              className="panel map-charging-context"
              aria-label="Charging stop under review"
            >
              <p className="eyebrow">CHARGING STOP UNDER REVIEW</p>
              <h2>
                {chargingBayName(offer.bay_id, focusStation?.properties.name)}
              </h2>
              <p>
                {offer.off_route
                  ? "Requires a detour"
                  : "Along the assigned route"}
              </p>
              <p className="fine">
                {estimate?.bay_id === offer.bay_id
                  ? estimate.station_state === "available"
                    ? "Bay available"
                    : "Check availability in the current offer"
                  : "Earlier offer · Review current availability before approving"}
              </p>
              {loadedMap && !focusPoint && (
                <p className="fine">This bay has no verified map position.</p>
              )}
              {review && (
                <button type="button" onClick={review}>
                  Return to charging decision
                </button>
              )}
            </section>
          )}
          <section className="panel position-card gradient cyan">
            <Icon name="Map" />
            <h2>
              {snap.monitoring_closed
                ? "Final recorded position"
                : forecast
                  ? last?.sim_time_s === forecast.payload.cutoff_working_s
                    ? "Position at forecast cutoff"
                    : "Last position before cutoff"
                  : position
                    ? "Observed position"
                    : "Last valid position"}
            </h2>
            <strong>
              {(position?.coordinates ?? last?.coordinates)
                ? `${Math.abs((position?.coordinates ?? last?.coordinates)![1]).toFixed(5)}° ${(position?.coordinates ?? last?.coordinates)![1] < 0 ? "S" : "N"}`
                : "Unavailable"}
              <br />
              {(position?.coordinates ?? last?.coordinates)
                ? `${Math.abs((position?.coordinates ?? last?.coordinates)![0]).toFixed(5)}° ${(position?.coordinates ?? last?.coordinates)![0] < 0 ? "W" : "E"}`
                : ""}
            </strong>
            <p role="status">
              {snap.monitoring_closed
                ? "Monitoring ended"
                : forecast
                  ? "Recorded observation"
                  : position
                    ? `Location ${position.freshness}`
                    : last
                      ? "Current location unavailable · historical position"
                      : "Location unknown"}
            </p>
            <small>
              Observed {time(position?.observed_at ?? last?.observed_at)}
              <br />
              {(position?.observed_at ?? last?.observed_at)?.slice(0, 10)}
              <br />
              {value(
                forecast?.payload.cutoff_working_s ??
                  position?.sim_time_s ??
                  last?.sim_time_s,
              )}{" "}
              working seconds
            </small>
          </section>
          <section className="panel">
            <h2>Map layers</h2>
            {!forecast && (
              <label className="map-trajectory-window">
                Trajectory window
                <select
                  value={trajectoryWindow}
                  onChange={(event) =>
                    setTrajectoryWindow(
                      event.target.value === "recent" ? "recent" : "full",
                    )
                  }
                >
                  <option value="full">Full trip</option>
                  <option value="recent">Last 30 working minutes</option>
                </select>
              </label>
            )}
            <MapLayers
              layers={layers}
              setLayers={setLayers}
              items={[
                { id: "trajectory", label: "Observed trajectory" },
                {
                  id: "route",
                  label: forecast
                    ? "Forecast remaining route"
                    : "Planned route",
                  hint: forecast
                    ? "Show the route used by this forecast."
                    : "Show the assigned route and any approved charging detour.",
                },
                {
                  id: "stops",
                  label: "Stops and charging bays",
                  related: ["stations"],
                  hint: "Show passenger stops, charging bays, and charging markers.",
                },
                { id: "labels", label: "Map labels" },
              ]}
            >
              {!forecast && (
                <button
                  aria-pressed={follow}
                  title="Keep the map centered on the moving bus."
                  onClick={() => setFollow(!follow)}
                >
                  {follow ? "Following vehicle" : "Follow vehicle"}
                </button>
              )}
            </MapLayers>
            {!forecast && last && (
              <button
                className="secondary map-fit-action"
                type="button"
                title="Fit the loaded observed trail inside the map."
                onClick={() => {
                  const bounds = L.latLngBounds([]);
                  for (const { coordinates } of points) {
                    if (coordinates)
                      bounds.extend([coordinates[1], coordinates[0]]);
                  }
                  if (bounds.isValid()) {
                    setFollow(false);
                    setLayers((layers) =>
                      layers.includes("trajectory")
                        ? layers
                        : [...layers, "trajectory"],
                    );
                    map.current?.fitBounds(bounds, {
                      padding: [35, 35],
                      maxZoom: 17,
                    });
                  }
                }}
              >
                Fit observed trail
              </button>
            )}
          </section>
          <section className="panel gradient charcoal">
            <SectionHeading title="Route context" icon="layers" tone="blue" />
            <dl className="key-values">
              <dt>Vehicle</dt>
              <dd>{snap.asset_id}</dd>
              {!forecast && (
                <>
                  {progress && (
                    <>
                      <dt>Estimated distance remaining</dt>
                      <dd>
                        {progress.remaining_m === null
                          ? "Unavailable"
                          : `${value(progress.remaining_m / 1000)} km`}
                      </dd>
                      <dt>
                        {progress.at_passenger_stop
                          ? "At passenger stop"
                          : "Next passenger stop"}
                      </dt>
                      <dd>
                        {passengerStopLabel(context, progress.next_stop_id)}
                      </dd>
                    </>
                  )}
                  <dt>Estimated time to arrival</dt>
                  <dd>{arrival ?? "Unavailable"}</dd>
                  {isCharging && (
                    <>
                      <dt>Vehicle activity</dt>
                      <dd>
                        <Icon name="battery" size={17} /> Charging
                      </dd>
                    </>
                  )}
                </>
              )}
              <dt>Source</dt>
              <dd>
                {forecast
                  ? "Saved forecast input"
                  : connected || snap.monitoring_closed
                    ? (snap.items[0]?.source_state ?? "unknown")
                    : "disconnected"}
              </dd>
              <dt>{forecast ? "Forecast cutoff" : "Heading"}</dt>
              <dd>
                {forecast ? (
                  `${value(forecast.payload.cutoff_working_s)} working s`
                ) : (
                  <>
                    {value(
                      snap.items.find((i) => i.signal_id === "motion.heading")
                        ?.observation?.sample.value,
                    )}{" "}
                    rad
                  </>
                )}
              </dd>
              <dt>Position accuracy</dt>
              <dd>
                {position?.accuracy == null
                  ? "Unavailable"
                  : `${value(position.accuracy)} m`}
              </dd>
            </dl>
          </section>
        </aside>
      </div>
      <section className="panel map-details">
        {loadingTrack && <Loading label="Loading observed trajectory…" />}
        <p className="fine">
          {points.filter((p) => p.coordinates).length} paired observed points ·
          {forecast
            ? "30 working minutes through forecast cutoff"
            : trajectoryWindow === "full"
              ? "full trip through latest recording"
              : "last 30 working minutes"}{" "}
          · gaps preserved
        </p>
        {error && <p role="alert">{error}</p>}
        {context && (
          <details className="raw">
            <summary>Mission, stop, and station details</summary>
            <p>
              {context.mission_id}
              {!forecast && " · assigned trip"}
            </p>
            <ul>
              {context.features
                .filter((f) =>
                  ["stops", "stations"].includes(f.properties.layer),
                )
                .map((f) => (
                  <li key={f.properties.id}>
                    {f.properties.name} ·{" "}
                    {f.properties.layer === "stations"
                      ? "Charging access and availability unverified"
                      : `Scheduled stop · ${f.properties.duration_s} s`}
                  </li>
                ))}
            </ul>
            <p>
              {context.attribution}. Planned lane geometry illustrates the
              assignment; actual lane choice is observed separately.
            </p>
          </details>
        )}
      </section>
    </>
  );
}
