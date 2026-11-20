import type { Dispatch, SetStateAction } from "react";

export function MapLayers({
  layers,
  setLayers,
  items,
  children,
}: {
  layers: string[];
  setLayers: Dispatch<SetStateAction<string[]>>;
  items: { id: string; label: string; related?: string[]; hint?: string }[];
  children?: React.ReactNode;
}) {
  return (
    <div className="map-layer-switches">
      {items.map(({ id, label, related = [], hint }) => (
        <label key={id} title={hint}>
          <span>{label}</span>
          <input
            type="checkbox"
            role="switch"
            checked={layers.includes(id)}
            onChange={() =>
              setLayers((old) =>
                old.includes(id)
                  ? old.filter(
                      (layer) => layer !== id && !related.includes(layer),
                    )
                  : [...new Set([...old, id, ...related])],
              )
            }
          />
        </label>
      ))}
      {children}
    </div>
  );
}

export function MapLegend({
  items,
}: {
  items: { tone: string; label: string; icon?: string }[];
}) {
  return (
    <div className="route-legend" aria-label="Map legend">
      {items.map(({ tone, label, icon }) => (
        <span key={tone}>
          <i className={tone} aria-hidden="true">
            {icon}
          </i>
          {label}
        </span>
      ))}
    </div>
  );
}
