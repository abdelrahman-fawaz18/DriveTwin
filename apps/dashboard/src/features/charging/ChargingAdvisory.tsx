import type { Advisory } from "./useChargingAdvisory.ts";
import type { ReviewOffer } from "./ChargingAlert.tsx";
import { Icon } from "../../ui/Icon.tsx";
import { SectionHeading } from "../../ui/SectionHeading.tsx";
import { Loading } from "../../ui/Loading.tsx";
import { ChargingApproval } from "./ChargingApproval.tsx";
import {
  chargingBayName,
  tripEventLabel,
  type TripEvent,
} from "../trip/tripReview.ts";
import { time } from "../../monitoring/model.ts";
import { preferredChoice, type ChargingChoice } from "./chargingDecision.ts";
import { remainingArrival } from "../trip/arrival.ts";
import { ObservedChargingStop } from "./ObservedChargingStop.tsx";

const labels: Record<string, string> = {
  waiting: "Waiting for a reserve-risk forecast",
  running: "Comparing charging options",
  continue: "Continue on the assigned route",
  provisional_plan: "Provisional charging plan",
  no_plan_in_search_domain: "No feasible option among the evaluated bays",
  already_below_reserve: "The bus is already below reserve",
  baseline_unavailable: "The route forecast is unavailable",
  source_unavailable: "Waiting for current vehicle readings",
  historical: "Last estimate expired",
  search_budget_exhausted: "The comparison did not finish within its budget",
  incomplete: "The comparison ended early",
  failed: "The charging estimate could not be calculated",
};
const closedReview = new Set([
  "continue",
  "no_plan_in_search_domain",
  "already_below_reserve",
]);

const fmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const energyFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const percent = (number: number) => fmt.format(number * 100) + "%";
const seconds = (number: number) => fmt.format(number) + " s";
const utc = new Intl.DateTimeFormat("en-GB", {
  timeZone: "UTC",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

const exclusionLabels: Record<string, string> = {
  incompatible_bay: "Connector or stopping space is incompatible",
  unverified_approach: "The approach lane could not be verified",
  passenger_stop_conflict: "The bay conflicts with a required passenger stop",
  approach_unavailable_or_below_floor:
    "The drive to the bay could not be verified above the planning floor",
  station_status_unverified: "Station status could not be confirmed",
  station_unavailable: "No usable charging window",
  station_window_too_short: "The available window is too short",
  below_floor_while_waiting:
    "Waiting would take the battery below the planning floor",
  insufficient_power: "Charging power would not cover the on-board load",
  remaining_route_unavailable_or_below_floor:
    "The remaining drive could not be verified above the planning floor",
};

export function ChargingAdvisory({
  run,
  token,
  asset,
  connected,
  data: liveData,
  workingTime,
  openMap,
  onDecline,
  onApproved,
  onReviewAgain,
  declinedBay,
  bayName = chargingBayName,
  recentActions = [],
  inAlert = false,
  previousOffer,
  reviewPaused,
  arrival,
}: {
  run: string;
  token: string;
  asset: string;
  connected: boolean;
  data: Advisory | null;
  workingTime: number;
  openMap: (offer: ReviewOffer) => void;
  onDecline?: (bay: string, digest: string) => void;
  onApproved?: (command: string, choice: ChargingChoice) => void;
  onReviewAgain?: (bay: string) => void;
  declinedBay?: string;
  bayName?: (bay: string) => string;
  recentActions?: TripEvent[];
  inAlert?: boolean;
  previousOffer?: ReviewOffer | null;
  reviewPaused?: boolean;
  arrival?: string | null;
}) {
  const data: Advisory = liveData ?? { configured: false };
  if (!data.configured && !previousOffer)
    return (
      <section className="panel" aria-label="Charging Advisor">
        <h2>Charging Advisor</h2>
        <p>
          {liveData
            ? "Enable Charging Advisor in the launcher for your next trip."
            : "Checking charging availability…"}
        </p>
      </section>
    );
  const effect = data.observed_effect;
  const estimate = connected ? data.estimate : null;
  const offer = estimate ?? previousOffer;
  const status = connected ? (data.status ?? "waiting") : "source_unavailable";
  const allowsQueued = (state?: string, offRoute?: boolean) =>
    data.queue_qualification && state === "occupied" && !offRoute;
  const choices: ChargingChoice[] = [];
  const recommended = estimate && preferredChoice(estimate);
  if (
    estimate &&
    recommended &&
    !estimate.past_approval_point &&
    (!estimate.station_state ||
      estimate.station_state === "available" ||
      allowsQueued(estimate.station_state, estimate.off_route))
  )
    choices.push(recommended);
  if (
    estimate?.fallback_bay_id &&
    estimate.fallback_sha256 &&
    !estimate.fallback_past_approval_point &&
    (!estimate.fallback_station_state ||
      estimate.fallback_station_state === "available" ||
      allowsQueued(
        estimate.fallback_station_state,
        estimate.fallback_off_route,
      ))
  )
    choices.push({
      bay: estimate.fallback_bay_id,
      digest: estimate.fallback_sha256,
      targetSoc: estimate.fallback_details?.target_soc,
      terms: estimate.fallback_details && {
        minimumSoc: estimate.fallback_details.minimum_soc,
        waitAndChargeS:
          estimate.fallback_details.wait_working_s +
          estimate.fallback_details.charging_working_s,
        arrivalWorkingS: estimate.fallback_details.arrival_working_s,
        offRoute: Boolean(estimate.fallback_off_route),
        detour: estimate.fallback_detour_sha256,
        stationState: estimate.fallback_station_state,
      },
    });
  return (
    <section
      className="panel operator-panel charging-advisor"
      aria-label="Charging Advisor"
    >
      {!inAlert && (
        <header className="charge-heading">
          <SectionHeading title="Charging Advisor" icon="Energy" tone="teal" />
          <span className="charge-mode">
            {data.automatic_qualification
              ? "Automatic trial"
              : data.can_respond
                ? "Operator approval enabled"
                : "Advice only"}
          </span>
        </header>
      )}
      {offer && !effect && (
        <div
          className={`charge-recommendation${estimate ? "" : " is-previous"}`}
        >
          <div>
            <small>
              {!estimate
                ? "Last recommendation"
                : offer.fallback_only
                  ? "Alternate charging stop"
                  : "Recommended charging stop"}
            </small>
            <div className="charge-offer-title">
              <h3>{bayName(offer.bay_id)}</h3>
              <button
                type="button"
                className="secondary"
                onClick={() => openMap(offer)}
                title="Locate this charging bay on the trip map"
              >
                Show on map
              </button>
            </div>
            <p>
              {offer.off_route
                ? "Detour from the assigned route"
                : "Along the assigned route"}
              {offer.station_state
                ? ` · ${!estimate ? "Last reported " : ""}${offer.station_state === "available" && estimate ? "Available" : offer.station_state.replaceAll("_", " ")}`
                : ""}
            </p>
          </div>
          <dl className="charge-summary">
            <div className="charge-target">
              <dt>
                <Icon name="battery" size={15} /> Charge to
              </dt>
              <dd>{percent(offer.target_soc)}</dd>
            </div>
            <div className="charge-duration">
              <dt>
                <Icon name="clock" size={15} /> Charging duration
              </dt>
              <dd>
                {fmt.format(offer.charging_working_s)}{" "}
                <span className="charge-summary-unit">working s</span>
              </dd>
            </div>
            <div className="charge-minimum">
              <dt title="Lowest predicted charge if this stop is taken">
                <Icon name="shield" size={15} /> Lowest charge
              </dt>
              <dd>{percent(offer.minimum_soc)}</dd>
            </div>
          </dl>
          {estimate && (
            <p className="charge-offer-arrival">
              Estimated time to destination with this stop ·{" "}
              {remainingArrival(estimate.arrival_working_s, workingTime) ??
                "Unavailable"}
            </p>
          )}
        </div>
      )}
      {data.can_respond && status !== "startup_delay" && (
        <ChargingApproval
          run={run}
          asset={asset}
          token={connected ? token : ""}
          fence={estimate?.job_fence}
          offerExpiryMs={estimate?.clientExpiryMs}
          preferredWorkingExpiry={estimate?.working_expires}
          fallbackWorkingExpiry={estimate?.fallback_details?.working_expires}
          preferredBay={estimate?.bay_id}
          workingTime={workingTime}
          choices={effect ? [] : choices}
          hasRecommendation={Boolean(offer && !effect)}
          reviewClosed={closedReview.has(status) || Boolean(effect)}
          reviewOffer={
            !effect &&
            !closedReview.has(status) &&
            offer &&
            !offer.fallback_only &&
            offer.station_state === "available"
              ? preferredChoice(offer)
              : undefined
          }
          reviewPaused={reviewPaused}
          waitingForRoad={estimate?.approval_waiting_for_road}
          waitingForMotion={estimate?.approval_waiting_for_motion}
          passedApprovalPoint={
            estimate?.past_approval_point && choices.length === 0
          }
          requireChoice={
            estimate?.fallback_only ||
            estimate?.past_approval_point ||
            (estimate?.station_state === "occupied" &&
              !allowsQueued(estimate.station_state, estimate.off_route))
          }
          automatic={data.automatic_qualification}
          bayName={bayName}
          declinedBay={declinedBay}
          onReviewAgain={onReviewAgain}
          onDecline={onDecline}
          onApproved={onApproved}
        />
      )}
      {!inAlert && recentActions.length > 0 && (
        <section
          className="charge-outcomes"
          aria-label="Charging decision timeline"
        >
          <h3>Charging decision timeline</h3>
          <ol className="trip-timeline">
            {recentActions.map((event) => (
              <li key={event.id}>
                <strong>{tripEventLabel(event)}</strong>
                <span>
                  {fmt.format(event.working)} working s · {time(event.at)}
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}
      {estimate &&
        !effect &&
        !data.can_respond &&
        !data.automatic_qualification && (
          <p className="muted">
            {data.operator_control
              ? "You signed in with read-only access. Copy the Dashboard key from Current session in the launcher, then disconnect and sign in again to approve a charging stop."
              : "Advice only. The bus will continue on its planned route."}
          </p>
        )}
      {data.operator_recovery && data.can_respond && (
        <ChargingApproval
          run={run}
          asset={asset}
          token={connected ? token : ""}
          choices={[]}
          recovery
        />
      )}
      {!effect &&
        !estimate &&
        (status === "running" ? (
          <Loading label="Comparing charging options…" />
        ) : (
          <p className="muted" role="status">
            {status === "startup_delay"
              ? `Charging advice starts at working second ${data.startup_delay_working_s}.`
              : (labels[status] ?? labels.source_unavailable)}
          </p>
        ))}
      {estimate && !effect ? (
        <details className="charge-details">
          <summary>Charging and trip estimates</summary>
          {data.operator_control && estimate.station_state === "occupied" && (
            <p role="status">
              The preferred bay is occupied. The forecast includes its wait.
              {allowsQueued(estimate.station_state, estimate.off_route)
                ? " The queued trial allows approval of this scheduled stop."
                : " This offer is advisory while the bay is occupied."}
            </p>
          )}
          <div className="charge-estimates">
            <section
              className="charge-estimate-row charger-arrival"
              aria-label="Arrive at the charger"
            >
              <div>
                <h4>Arrive at the charger</h4>
                {estimate.charger_arrival_working_s !== undefined && (
                  <p>
                    Working second{" "}
                    {fmt.format(estimate.charger_arrival_working_s)}
                  </p>
                )}
              </div>
              <dl>
                <div>
                  <dt>Battery charge</dt>
                  <dd>
                    {estimate.charger_arrival_soc === undefined
                      ? "—"
                      : percent(estimate.charger_arrival_soc)}
                  </dd>
                </div>
                <div>
                  <dt>Wait and setup</dt>
                  <dd>
                    {fmt.format(estimate.wait_working_s)}{" "}
                    <small>working s</small>
                  </dd>
                </div>
              </dl>
            </section>
            <section
              className="charge-estimate-row charger-transfer"
              aria-label="Charge at the bay"
            >
              <div>
                <h4>Charge at the bay</h4>
                {estimate.estimated_grid_energy_j !== undefined && (
                  <p>
                    {energyFmt.format(
                      estimate.estimated_grid_energy_j / 3_600_000,
                    )}{" "}
                    kWh from charger
                  </p>
                )}
              </div>
              <dl>
                <div>
                  <dt>Departure charge</dt>
                  <dd>{percent(estimate.target_soc)}</dd>
                </div>
                <div>
                  <dt>Charging duration</dt>
                  <dd>
                    {fmt.format(estimate.charging_working_s)}{" "}
                    <small>working s</small>
                  </dd>
                </div>
              </dl>
            </section>
            <section
              className="charge-estimate-row trip-arrival"
              aria-label="Finish the trip"
            >
              <div>
                <h4>Finish the trip</h4>
                <p>Working second {fmt.format(estimate.arrival_working_s)}</p>
              </div>
              <dl>
                <div>
                  <dt>Arrival charge</dt>
                  <dd>
                    {estimate.destination_soc === undefined
                      ? "—"
                      : percent(estimate.destination_soc)}
                  </dd>
                </div>
                <div>
                  <dt>Lowest charge with stop</dt>
                  <dd>{percent(estimate.minimum_soc)}</dd>
                </div>
              </dl>
            </section>
            {estimate.crossing_working_s !== null && (
              <section
                className="charge-estimate-note reserve"
                aria-label="Without the stop"
              >
                <h4>Without the stop</h4>
                <p>
                  First predicted reserve crossing at working second{" "}
                  <strong>{fmt.format(estimate.crossing_working_s)}</strong>.
                </p>
              </section>
            )}
            {estimate.working_expires !== undefined &&
              estimate.expires_at_real_life_s !== undefined && (
                <section
                  className="charge-estimate-note"
                  aria-label="Review window"
                >
                  <header>
                    <h4>Review window</h4>
                    <span>
                      {fmt.format(estimate.working_expires)} working s or{" "}
                      {utc.format(estimate.expires_at_real_life_s * 1000)} UTC
                    </span>
                  </header>
                  <p>
                    Whichever comes first. Route or bay changes can close the
                    window sooner.
                  </p>
                </section>
              )}
          </div>
          {estimate.fallback_bay_id && !estimate.fallback_details && (
            <p>
              Fallback · {bayName(estimate.fallback_bay_id)}
              {estimate.fallback_off_route ? " · Off-route stop" : ""}
            </p>
          )}
          {estimate.fallback_bay_id && estimate.fallback_details && (
            <details>
              <summary>
                Compare fallback · {bayName(estimate.fallback_bay_id)}
                {estimate.fallback_off_route ? " · Off-route stop" : ""}
              </summary>
              <dl className="charging-advisory-details">
                <dt>Departure target</dt>
                <dd>{percent(estimate.fallback_details.target_soc)}</dd>
                <dt>Lowest predicted charge</dt>
                <dd>{percent(estimate.fallback_details.minimum_soc)}</dd>
                <dt>Wait and setup</dt>
                <dd>
                  {seconds(estimate.fallback_details.wait_working_s)} working
                  time
                </dd>
                <dt>Charging duration</dt>
                <dd>
                  {seconds(estimate.fallback_details.charging_working_s)}{" "}
                  working time
                </dd>
                {estimate.fallback_details.working_expires !== undefined && (
                  <>
                    <dt>Working-time limit</dt>
                    <dd>
                      Before working second{" "}
                      {fmt.format(estimate.fallback_details.working_expires)}
                    </dd>
                  </>
                )}
                <dt>Estimated destination arrival</dt>
                <dd>
                  Working second{" "}
                  {fmt.format(estimate.fallback_details.arrival_working_s)}
                </dd>
              </dl>
            </details>
          )}
        </details>
      ) : !effect && !previousOffer && data.last_offer ? (
        <p className="charge-last-offer">
          Previous recommendation: {data.last_offer.bay_id}, charge to{" "}
          {percent(data.last_offer.target_soc)}.{" "}
          {data.can_respond && "Approval requires a current offer."}
        </p>
      ) : !effect && data.recorded ? (
        <p className="muted">
          Last comparison used working second{" "}
          {fmt.format(data.recorded.cutoff_working_s)}.
        </p>
      ) : null}
      {connected && !effect && Boolean(data.excluded_bays?.length) && (
        <details>
          <summary>Limits found for other bays</summary>
          <dl className="charging-advisory-details">
            {data.excluded_bays?.map(({ bay_id, reasons }) => (
              <div key={bay_id}>
                <dt>{bayName(bay_id)}</dt>
                <dd>
                  {reasons
                    .map(
                      (reason) =>
                        exclusionLabels[reason] ??
                        "A required input could not be verified",
                    )
                    .join(" · ")}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      {effect && (
        <ObservedChargingStop
          effect={effect}
          bayName={bayName}
          arrival={arrival ?? "Unavailable"}
        />
      )}
    </section>
  );
}
