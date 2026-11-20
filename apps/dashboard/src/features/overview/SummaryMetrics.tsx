import type { Snapshot } from "../../monitoring/model.ts";
import { value } from "../../monitoring/model.ts";
import { summaryCards, summaryReading } from "./overview.ts";
import { Icon } from "../../ui/Icon.tsx";
export function SummaryMetrics({
  snap,
  connected,
  recorded = false,
  compact = false,
}: {
  snap: Snapshot;
  connected: boolean;
  recorded?: boolean;
  compact?: boolean;
}) {
  return (
    <div className={`metrics-row ${compact ? "compact-metrics" : ""}`}>
      {summaryCards.map((card, n) => {
        const r = snap.items.find((i) => i.signal_id === card.signal),
          display = summaryReading(card, r),
          fresh = connected ? r?.freshness : "stale";
        return (
          <section
            key={card.signal}
            className={`metric metric-card gradient ${["pink", "violet", "cyan", "amber"][n]} ${fresh === "stale" ? "is-stale" : ""}`}
          >
            <div className="metric-top">
              <span>{card.title}</span>
              <Icon
                name={["battery", "Overview", "layers", "temperature"][n]!}
              />
            </div>
            <div className="metric-number">
              {compact && display.value != null
                ? display.value.toLocaleString("en-US", {
                    maximumFractionDigits: n === 3 ? 1 : 2,
                  })
                : value(display.value)}
              <small>{display.unit}</small>
            </div>
            <div
              className="metric-foot"
              data-quality={r?.quality}
              data-freshness={fresh}
            >
              {r ? (
                <>
                  <span className={`badge ${r.quality}`}>{r.quality}</span>
                  <span className={`badge ${fresh}`}>
                    {recorded ? "Recorded" : fresh}
                  </span>
                </>
              ) : (
                "Awaiting readings"
              )}
            </div>
            <small className="metric-storage-note">
              {display.supported
                ? display.stored === "1"
                  ? "Stored as a fraction"
                  : `Stored in ${display.stored}`
                : "No supported reading yet"}
            </small>
          </section>
        );
      })}
    </div>
  );
}
