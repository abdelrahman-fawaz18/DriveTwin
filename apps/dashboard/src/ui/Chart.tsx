import { useId, useState } from "react";
type Point = { t: number; value: number | null };
const number = (n: number | null | undefined, digits = 2) =>
  n == null
    ? "—"
    : new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(
        n,
      );

export function Chart({
  points,
  color,
  unit,
  cursor,
  onCursor,
  onSeek,
  height = 215,
  width = 800,
  tickPrecision,
  domain,
  reserve,
  band,
  label,
}: {
  points: Point[];
  color: string;
  unit: string;
  cursor?: number;
  onCursor?: (t: number) => void;
  onSeek?: (t: number) => void;
  height?: number;
  width?: number;
  tickPrecision?: number;
  domain?: [number, number];
  reserve?: number;
  band?: number;
  label: string;
}) {
  const id = useId().replaceAll(":", "");
  const [hover, setHover] = useState<number | undefined>();
  const left = 62,
    right = 20,
    top = 20,
    bottom = 38;
  const values = points.flatMap((p) => (p.value == null ? [] : [p.value]));
  const min = domain?.[0] ?? Math.min(...values),
    max = domain?.[1] ?? Math.max(...values);
  const pad = domain
    ? 0
    : Math.max((max - min) * 0.18, Math.abs(max) * 0.001, 0.01);
  const lo = min - pad,
    hi = max + pad;
  const tickDigits =
    tickPrecision ??
    (hi - lo < 0.1 ? 5 : hi - lo < 1 ? 3 : hi - lo < 10 ? 2 : 1);
  const tickLabel = (value: number) =>
    Math.abs(value) >= 10000
      ? new Intl.NumberFormat("en-US", {
          notation: "compact",
          maximumFractionDigits: 2,
        }).format(value)
      : number(value, tickDigits);
  const from = points[0]?.t ?? 0,
    to = points.at(-1)?.t ?? 1;
  const x = (t: number) =>
    left + ((t - from) / Math.max(1, to - from)) * (width - left - right);
  const y = (v: number) =>
    height -
    bottom -
    ((v - lo) / Math.max(0.000001, hi - lo)) * (height - top - bottom);
  const runs: Point[][] = [];
  for (const p of points) {
    if (p.value == null) {
      if (runs.at(-1)?.length) runs.push([]);
    } else {
      if (!runs.length) runs.push([]);
      runs.at(-1)!.push(p);
    }
  }
  const selected = points.find(
    (p) => p.t === Math.round(cursor ?? hover ?? -1),
  );
  const path = (run: Point[]) =>
    run.map((p, i) => `${i ? "L" : "M"}${x(p.t)},${y(p.value!)}`).join(" ");
  if (!values.length)
    return (
      <div className="chart-empty" style={{ height }}>
        No valid readings in this window.
      </div>
    );
  return (
    <div className="dt-chart" style={{ color }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={label}
        onPointerMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const t = Math.round(
            from +
              Math.max(
                0,
                Math.min(
                  1,
                  (((event.clientX - rect.left) / rect.width) * width - left) /
                    (width - left - right),
                ),
              ) *
                (to - from),
          );
          setHover(t);
          onCursor?.(t);
        }}
        onPointerLeave={() => setHover(undefined)}
        onClick={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const ratio = Math.max(
            0,
            Math.min(
              1,
              (((event.clientX - rect.left) / rect.width) * width - left) /
                (width - left - right),
            ),
          );
          onSeek?.(Math.round(from + ratio * (to - from)));
        }}
      >
        <defs>
          <linearGradient id={`fill-${id}`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity=".22" />
            <stop offset="100%" stopColor={color} stopOpacity=".015" />
          </linearGradient>
        </defs>
        {Array.from({ length: 5 }, (_, i) => {
          const v = lo + ((hi - lo) * i) / 4;
          return (
            <g key={i}>
              <line
                x1={left}
                y1={y(v)}
                x2={width - right}
                y2={y(v)}
                className="chart-grid"
              />
              <text x={left - 12} y={y(v) + 4} textAnchor="end">
                {tickLabel(v)}
              </text>
            </g>
          );
        })}
        {Array.from({ length: 7 }, (_, i) => {
          const t = from + ((to - from) * i) / 6;
          return (
            <g key={i}>
              <line
                x1={x(t)}
                y1={top}
                x2={x(t)}
                y2={height - bottom}
                className="chart-grid vertical"
              />
              <text x={x(t)} y={height - 17} textAnchor="middle">
                {number(t, 0)}
              </text>
            </g>
          );
        })}
        {band != null &&
          runs.map(
            (run, i) =>
              run.length > 1 && (
                <path
                  key={i}
                  d={
                    run
                      .map(
                        (p, j) =>
                          `${j ? "L" : "M"}${x(p.t)},${y(p.value! + band)}`,
                      )
                      .join(" ") +
                    " " +
                    [...run]
                      .reverse()
                      .map((p) => `L${x(p.t)},${y(p.value! - band)}`)
                      .join(" ") +
                    " Z"
                  }
                  fill="#9b77d7"
                  fillOpacity=".19"
                />
              ),
          )}
        {runs.map(
          (run, i) =>
            run.length > 0 && (
              <g key={i}>
                {band == null && (
                  <path
                    d={`${path(run)} L${x(run.at(-1)!.t)},${height - bottom} L${x(run[0]!.t)},${height - bottom}Z`}
                    fill={`url(#fill-${id})`}
                  />
                )}
                <path
                  d={path(run)}
                  stroke={color}
                  strokeWidth="2.5"
                  fill="none"
                  strokeLinejoin="round"
                />
              </g>
            ),
        )}
        {reserve != null && (
          <g>
            <line
              x1={left}
              x2={width - right}
              y1={y(reserve)}
              y2={y(reserve)}
              stroke="#c58a19"
              strokeWidth="1.5"
              strokeDasharray="6 5"
            />
            <text x={width - right - 4} y={y(reserve) - 7} textAnchor="end">
              Reserve {reserve}%
            </text>
          </g>
        )}
        {selected && (
          <g>
            <line
              x1={x(selected.t)}
              x2={x(selected.t)}
              y1={top}
              y2={height - bottom}
              stroke="#6f7686"
              strokeDasharray="3 3"
            />
            {selected.value != null && (
              <circle
                cx={x(selected.t)}
                cy={y(selected.value)}
                r="4.5"
                stroke={color}
                strokeWidth="2"
                fill="white"
              />
            )}
          </g>
        )}
      </svg>
      <div className="chart-footer">
        <span>{unit}</span>
        <span>Working time (s)</span>
        <span>
          {selected
            ? `${selected.t} s · ${number(selected.value, Math.abs(selected.value ?? 0) < 1 ? 4 : 2)} ${unit}`
            : "Move across chart to inspect"}
        </span>
      </div>
    </div>
  );
}

export function Bars({
  items,
  unit,
  maximum,
}: {
  items: { label: string; value: number | null; color: string }[];
  unit: string;
  maximum?: number;
}) {
  const values = items.flatMap((item) =>
    item.value == null ? [] : [item.value],
  );
  const min = Math.min(0, ...values) * 1.1;
  const max = maximum ?? Math.max(1, Math.max(0, ...values) * 1.15);
  const span = Math.max(1, max - min);
  const position = (value: number) => ((value - min) / span) * 100;
  return (
    <div className="dt-bars" aria-label={`Comparison in ${unit}`}>
      {items.map((item) => (
        <div className="bar-row" key={item.label}>
          <span>{item.label}</span>
          <div className="bar-track">
            <i className="bar-zero" style={{ left: `${position(0)}%` }} />
            {item.value != null && (
              <div
                style={{
                  left: `${position(Math.min(0, item.value))}%`,
                  width: `${Math.min(100, (Math.abs(item.value) / span) * 100)}%`,
                  background: `linear-gradient(110deg, ${item.color}, color-mix(in srgb, ${item.color}, white 23%))`,
                }}
              />
            )}
          </div>
          <strong>
            {number(item.value, 1)}
            <small> {unit}</small>
          </strong>
        </div>
      ))}
      <div className="bar-scale">
        <span>
          {number(min, 1)} {unit}
        </span>
        <span>{number((min + max) / 2, 1)}</span>
        <span>
          {number(max, 1)} {unit}
        </span>
      </div>
    </div>
  );
}
