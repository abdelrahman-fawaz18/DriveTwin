import { SectionIcon } from "../../ui/SectionHeading.tsx";
import { chargingBayName } from "../trip/tripReview.ts";
import type { Advisory } from "./useChargingAdvisory.ts";

const amount = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const stages = {
  approaching: "Approaching charging bay",
  waiting_for_charger: "Waiting for the charger to open",
  charging: "Charging observed",
  target_reached: "Departure charge reached",
  motion_resumed: "Driving resumed",
};

export function ObservedChargingStop({
  effect,
  bayName = chargingBayName,
  arrival,
  openAdvisor,
  forecastNote,
}: {
  effect: NonNullable<Advisory["observed_effect"]>;
  bayName?: (id: string) => string;
  arrival?: string | null;
  openAdvisor?: () => void;
  forecastNote?: string;
}) {
  const title = openAdvisor ? "Observed charging stop" : "Observed charging";
  const showTransfer =
    effect.state !== "approaching" && effect.state !== "waiting_for_charger";
  return (
    <section
      aria-label={title}
      className={openAdvisor ? "panel charging-observed" : "charging-observed"}
    >
      <header className="observed-charge-heading">
        <div className="observed-charge-title">
          <SectionIcon name="battery" tone="teal" />
          <div>
            <h3>{title}</h3>
            <p>{bayName(effect.bay_id)}</p>
          </div>
        </div>
        <span className="observed-charge-state">{stages[effect.state]}</span>
      </header>
      <dl className="observed-charge-readings">
        {showTransfer && (
          <div>
            <dt>Energy delivered to battery</dt>
            <dd>{amount.format(effect.delivered_j / 3_600_000)} kWh</dd>
          </div>
        )}
        <div>
          <dt>State observed at</dt>
          <dd>{amount.format(effect.observed_working_s)} working s</dd>
        </div>
        {arrival != null && (
          <div>
            <dt>Estimated time to arrival</dt>
            <dd>{arrival}</dd>
          </div>
        )}
      </dl>
      {(forecastNote || openAdvisor) && (
        <div className="observed-charge-footer">
          {forecastNote && <p>{forecastNote}</p>}
          {openAdvisor && (
            <button type="button" className="secondary" onClick={openAdvisor}>
              Open Charging Advisor
            </button>
          )}
        </div>
      )}
    </section>
  );
}
