import { useEffect, useRef, useState, type RefObject } from "react";
import { label } from "../../monitoring/model.ts";
import { measurementName, signalGroup } from "../../app/presentation.ts";

export function SignalPicker({
  items,
  selected,
  onSelect,
  disclosure,
}: {
  items: { id: string; unit: string }[];
  selected: string[];
  onSelect: (ids: string[]) => void;
  disclosure?: RefObject<HTMLDetailsElement | null>;
}) {
  const own = useRef<HTMLDetailsElement>(null);
  const ref = disclosure ?? own;
  const [search, setSearch] = useState("");
  const groups = new Map<string, typeof items>();
  const query = search.trim().toLowerCase();
  for (const item of items) {
    const { id } = item;
    if (
      !`${measurementName(id)} ${label(id)} ${id}`.toLowerCase().includes(query)
    )
      continue;
    const group = signalGroup(id);
    groups.set(group, [...(groups.get(group) ?? []), item]);
  }
  useEffect(() => {
    const close = (event: PointerEvent | KeyboardEvent) => {
      const element = ref.current;
      if (!element?.open) return;
      if (event instanceof KeyboardEvent) {
        if (event.key !== "Escape" || !element.contains(document.activeElement))
          return;
        element.querySelector("summary")?.focus();
      } else if (event.composedPath().includes(element)) return;
      element.open = false;
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [ref]);
  return (
    <details ref={ref} className="history-signal-picker">
      <summary title="Choose any number of signals. Each selection opens a separate chart.">
        Select signals to compare <span>{selected.length} selected</span>
      </summary>
      <div className="history-signal-menu">
        <div className="panel-head">
          <strong>Signals to display</strong>
          <button
            type="button"
            className="secondary"
            disabled={!selected.length}
            onClick={() => onSelect([])}
          >
            Clear selection
          </button>
        </div>
        <label>
          Search signals
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Name or signal identifier"
          />
        </label>
        <div className="signal-choices">
          {[...groups].map(([group, signals]) => (
            <fieldset key={group}>
              <legend>{group}</legend>
              {signals.map(({ id, unit }) => (
                <label key={id}>
                  <input
                    type="checkbox"
                    aria-label={`Compare ${id}`}
                    checked={selected.includes(id)}
                    onChange={() =>
                      onSelect(
                        selected.includes(id)
                          ? selected.filter((item) => item !== id)
                          : [...selected, id],
                      )
                    }
                  />
                  <span>
                    {measurementName(id)}
                    <small>{unit}</small>
                  </span>
                </label>
              ))}
            </fieldset>
          ))}
          {!groups.size && (
            <p className="fine">No signals match your search.</p>
          )}
        </div>
      </div>
    </details>
  );
}
