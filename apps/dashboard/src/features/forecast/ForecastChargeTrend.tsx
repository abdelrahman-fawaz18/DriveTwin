import type { SavedRouteForecast } from "./forecastNavigation.ts";
import { value } from "../../monitoring/model.ts";
function boundary(a: SavedRouteForecast, b: SavedRouteForecast) {
  const from = a.payload.scope,
    to = b.payload.scope;
  if (
    from.source_id !== to.source_id ||
    from.source_epoch !== to.source_epoch ||
    from.session_id !== to.session_id
  )
    return "Source changed";
  if (a.payload.model_version !== b.payload.model_version)
    return "Model changed";
  if (b.payload.cutoff_working_s <= a.payload.cutoff_working_s)
    return "Forecast time did not advance";
  return null;
}
export function forecastRuns(records: SavedRouteForecast[]) {
  const result: SavedRouteForecast[][] = [];
  for (const r of [...records].sort((a, b) => a.fence - b.fence)) {
    const previous = result.at(-1)?.at(-1);
    if (r.payload.status !== "available" || !r.payload.summary) {
      result.push([]);
      continue;
    }
    if (!previous || boundary(previous, r)) result.push([]);
    result.at(-1)!.push(r);
  }
  return result.filter((r) => r.length);
}
export function ForecastChargeTrend({
  records,
  reserve,
}: {
  records: SavedRouteForecast[];
  reserve: number | null;
}) {
  const runs = forecastRuns(records),
    rows = runs.flat();
  if (!rows.length)
    return (
      <section className="panel forecast-update-trend">
        <h3>Charge across forecast updates</h3>
        <p>No completed forecasts to chart.</p>
      </section>
    );
  const values = rows.flatMap((r) => [
    r.payload.summary!.arrival_soc!,
    r.payload.summary!.minimum_soc,
  ]);
  const min = Math.min(...values) * 100,
    max = Math.max(...values) * 100,
    pad = Math.max(0.01, (max - min) * 0.15);
  const lo = Math.max(0, min - pad),
    hi = Math.min(100, max + pad);
  const start = Math.min(...rows.map((r) => r.payload.cutoff_working_s)),
    end = Math.max(start + 1, ...rows.map((r) => r.payload.cutoff_working_s));
  const x = (t: number) => 60 + ((t - start) / (end - start)) * 590,
    y = (v: number) => 125 - ((v * 100 - lo) / (hi - lo || 1)) * 100;
  const reserveVisible =
    reserve !== null && reserve * 100 >= lo && reserve * 100 <= hi;
  const planChanges = rows.slice(1).filter((next, index) => {
    const previous = rows[index]!;
    return (
      !boundary(previous, next) &&
      Boolean(previous.route_revision && next.route_revision) &&
      previous.route_revision !== next.route_revision
    );
  });
  const gaps = runs.slice(1).map((run, index) => {
    const from = runs[index]!.at(-1)!,
      to = run[0]!;
    const skipped = records.some(
      (r) =>
        r.fence > from.fence &&
        r.fence < to.fence &&
        (r.payload.status !== "available" || !r.payload.summary),
    );
    return {
      from: from.payload.cutoff_working_s,
      to: to.payload.cutoff_working_s,
      reason: skipped
        ? "Forecast unavailable between these updates"
        : (boundary(from, to) ?? "Forecast series restarted"),
    };
  });
  return (
    <section className="panel forecast-update-trend">
      <h3>Charge across forecast updates</h3>
      <svg
        viewBox="0 0 680 160"
        role="img"
        aria-label="Predicted arrival and lowest charge by forecast working cutoff, with trip plan changes marked"
      >
        {[lo, (lo + hi) / 2, hi].map((v, i) => (
          <g key={i}>
            <line
              x1="60"
              x2="650"
              y1={y(v / 100)}
              y2={y(v / 100)}
              stroke="#dfe5ed"
              strokeDasharray="3 4"
            />
            <text x="52" y={y(v / 100) + 4} textAnchor="end">
              {v.toFixed(2)}%
            </text>
          </g>
        ))}
        {reserveVisible && (
          <line
            x1="60"
            x2="650"
            y1={y(reserve!)}
            y2={y(reserve!)}
            stroke="#956014"
            strokeDasharray="5 4"
          />
        )}
        {gaps.map((gap, index) => (
          <g key={index}>
            <line
              x1={x((gap.from + gap.to) / 2)}
              x2={x((gap.from + gap.to) / 2)}
              y1="21"
              y2="130"
              stroke="#8b6aa1"
              strokeDasharray="3 4"
            >
              <title>{gap.reason}</title>
            </line>
          </g>
        ))}
        {planChanges.map((record) => {
          const cutoff = record.payload.cutoff_working_s;
          const px = x(cutoff);
          return (
            <g key={record.fence} className="forecast-plan-marker">
              <title>
                First forecast on the updated trip plan at {value(cutoff)}{" "}
                working s
              </title>
              <line
                x1={px}
                x2={px}
                y1="18"
                y2="130"
                stroke="currentColor"
                strokeDasharray="3 4"
              />
              <path d={`M ${px} 7 l 5 5 -5 5 -5 -5 z`} fill="currentColor" />
            </g>
          );
        })}
        {runs.map((run, n) => (
          <g key={n}>
            {(["arrival_soc", "minimum_soc"] as const).map((key, i) => (
              <g key={key} fill={i ? "#2062b6" : "#b63258"}>
                <polyline
                  fill="none"
                  stroke={i ? "#2062b6" : "#b63258"}
                  strokeWidth="2"
                  strokeDasharray={i ? "4 3" : undefined}
                  points={run
                    .map(
                      (r) =>
                        `${x(r.payload.cutoff_working_s)},${y(r.payload.summary![key]!)}`,
                    )
                    .join(" ")}
                />
                {run.map((r) => (
                  <circle
                    key={r.fence}
                    cx={x(r.payload.cutoff_working_s)}
                    cy={y(r.payload.summary![key]!)}
                    r="3"
                  >
                    <title>
                      {value(r.payload.cutoff_working_s)} working s ·{" "}
                      {(r.payload.summary![key]! * 100).toFixed(2)}%
                    </title>
                  </circle>
                ))}
              </g>
            ))}
          </g>
        ))}
        <text x="60" y="151">
          {value(start)} s
        </text>
        <text x="650" y="151" textAnchor="end">
          {value(end)} s
        </text>
      </svg>
      <div className="forecast-trend-key">
        <span className="charge-arrival">━ Arrival</span>
        <span className="charge-lowest">┄ Lowest</span>
        {planChanges.length > 0 && (
          <span className="forecast-plan-marker">◆ Trip plan changed</span>
        )}
        <span>
          {reserve === null
            ? "Reserve unavailable"
            : `${(reserve * 100).toFixed(0)}% reserve${reserveVisible ? "" : " outside chart scale"}`}
        </span>
      </div>
      <p className="fine">
        Each dot uses the working time of the readings behind that forecast.
        {planChanges.length > 0 &&
          ` First ${planChanges.length === 1 ? "forecast" : "forecasts"} on an updated trip plan at ${planChanges.map((r) => value(r.payload.cutoff_working_s)).join(", ")} working s.`}
      </p>
      {gaps.length > 0 && (
        <details className="forecast-gap-notes">
          <summary>
            {gaps.length === 1
              ? "Why the line breaks"
              : `Why the line breaks (${gaps.length})`}
          </summary>
          <ul>
            {gaps.map((gap, index) => (
              <li key={index}>
                <strong>
                  {value(gap.from)}–{value(gap.to)} working s
                </strong>{" "}
                · {gap.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
