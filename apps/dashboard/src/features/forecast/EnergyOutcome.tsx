import type { EnergyView } from "./energyModel.ts";
import { value } from "../../monitoring/model.ts";

// The service returns an endpoint and a scenario envelope, not a time trajectory.
export function EnergyOutcome({ data }: { data: EnergyView }) {
  if (!data.values || !data.uncertainty) return null;
  const e = data.uncertainty,
    reserve = data.extensions?.explanation?.reserve_soc;
  const x = (soc: number) => 60 + soc * 660;
  return (
    <div className="forecast-comparison">
      <h3>Battery charge at route completion</h3>
      <svg
        viewBox="0 0 780 155"
        role="img"
        aria-label={`Estimated final charge ${value(data.values.projected_final_soc * 100)} percent; scenario range ${value(e.minimum_final_soc * 100)} to ${value(e.maximum_final_soc * 100)} percent`}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((n) => (
          <g key={n}>
            <line
              x1={x(n)}
              x2={x(n)}
              y1="20"
              y2="103"
              stroke="#e3e3ed"
              strokeDasharray="3 5"
            />
            <text
              x={x(n)}
              y="125"
              textAnchor="middle"
              fill="#6c7489"
              fontSize="11"
            >
              {n * 100}%
            </text>
          </g>
        ))}
        <line
          x1={x(e.minimum_final_soc)}
          x2={x(e.maximum_final_soc)}
          y1="65"
          y2="65"
          stroke="#e3c3de"
          strokeWidth="25"
          strokeLinecap="round"
        />
        <circle
          cx={x(data.values.projected_final_soc)}
          cy="65"
          r="9"
          fill="#d9277f"
          stroke="white"
          strokeWidth="3"
        />
        {reserve != null && (
          <g>
            <line
              x1={x(reserve)}
              x2={x(reserve)}
              y1="20"
              y2="105"
              stroke="#a56b26"
              strokeDasharray="5 5"
            />
            <text x={x(reserve) + 5} y="15" fill="#946322" fontSize="11">
              {value(reserve * 100)}% reserve
            </text>
          </g>
        )}
        <text x="390" y="150" textAnchor="middle" fill="#6c7489" fontSize="11">
          Battery charge (%)
        </text>
      </svg>
      <p className="energy-chart-note">
        Point: forecast · band: evaluated scenario range. The band is not a
        probability interval.
      </p>
    </div>
  );
}
