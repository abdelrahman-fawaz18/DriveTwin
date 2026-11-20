import { Fragment, useState } from "react";
import { SectionHeading } from "../../ui/SectionHeading.tsx";
import type { Reading, Snapshot } from "../../monitoring/model.ts";
import { label, time, value } from "../../monitoring/model.ts";
import {
  signalGroup,
  groupTone,
  measurementName,
} from "../../app/presentation.ts";
function Badge({ text }: { text: string }) {
  return <span className={`badge ${text}`}>{text}</span>;
}
export function Measurements({
  snap,
  connected,
  openHistory,
  recorded = false,
}: {
  snap: Snapshot;
  connected: boolean;
  openHistory: (id: string) => void;
  recorded?: boolean;
}) {
  const [search, setSearch] = useState(""),
    [quality, setQuality] = useState("all"),
    [component, setComponent] = useState("all"),
    [group, setGroup] = useState("all"),
    [freshness, setFreshness] = useState("all");
  const state = (i: Reading) => (connected ? i.freshness : "stale");
  const shown = snap.items.filter(
    (i) =>
      (quality === "all" || i.quality === quality) &&
      (component === "all" || i.definition.component_id === component) &&
      (group === "all" || signalGroup(i.signal_id) === group) &&
      (freshness === "all" || state(i) === freshness) &&
      `${measurementName(i.signal_id)} ${label(i.signal_id)} ${i.signal_id} ${i.source_id} ${i.definition.unit} ${i.definition.classification} ${i.definition.physical_location}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const advancedFilters = (
    <>
      <label>
        Freshness
        <select
          aria-label="Freshness"
          value={freshness}
          onChange={(e) => setFreshness(e.target.value)}
        >
          <option value="all">All freshness</option>
          {["fresh", "paused", "stale", "unknown"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </label>
      <label>
        Component
        <select
          aria-label="Component"
          value={component}
          onChange={(e) => setComponent(e.target.value)}
        >
          <option value="all">All components</option>
          {[...new Set(snap.items.map((i) => i.definition.component_id))].map(
            (c) => (
              <option key={c}>{c}</option>
            ),
          )}
        </select>
      </label>
      <label>
        Quality
        <select
          aria-label="Quality"
          value={quality}
          onChange={(e) => setQuality(e.target.value)}
        >
          <option value="all">All quality</option>
          {["good", "invalid", "missing", "unknown"].map((q) => (
            <option key={q}>{q}</option>
          ))}
        </select>
      </label>
    </>
  );
  return (
    <>
      <section className="panel measurements">
        <div className="panel-head">
          <SectionHeading
            eyebrow={recorded ? undefined : "CATALOG-DRIVEN MONITORING"}
            icon="Measurements"
            tone="teal"
            title={
              <>
                Registered measurements{" "}
                <span className="count">{snap.items.length}</span>
              </>
            }
          />
          {recorded ? (
            <span className="recorded-quality-summary">
              {snap.items.filter((i) => i.observation).length} observed ·{" "}
              {snap.items.length} registered
              {snap.items.some(
                (i) =>
                  i.quality === "missing" ||
                  i.quality === "invalid" ||
                  state(i) === "stale",
              ) && (
                <strong className="notice warning">
                  Review quality or age
                </strong>
              )}
            </span>
          ) : (
            <span className="coverage-pill gradient pink">
              Fresh {snap.items.filter((i) => state(i) === "fresh").length}{" "}
              <span>
                Stale {snap.items.filter((i) => state(i) === "stale").length}
              </span>
            </span>
          )}
        </div>
        <div className="filters measurement-filters">
          <label className="search-label">
            Search measurements
            <input
              aria-label="Search measurements"
              placeholder="Signal, unit, source, or measurement type…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <label>
            Measurement group
            <select
              aria-label="Measurement group"
              value={group}
              onChange={(e) => setGroup(e.target.value)}
            >
              <option value="all">All groups</option>
              {[
                ...new Set(snap.items.map((i) => signalGroup(i.signal_id))),
              ].map((g) => (
                <option key={g}>{g}</option>
              ))}
            </select>
          </label>
          {recorded ? (
            <details className="recorded-measurement-filters">
              <summary>
                More filters
                {[freshness, component, quality].filter((v) => v !== "all")
                  .length > 0
                  ? ` (${[freshness, component, quality].filter((v) => v !== "all").length})`
                  : ""}
              </summary>
              <div>{advancedFilters}</div>
            </details>
          ) : (
            advancedFilters
          )}
          {recorded &&
            (search ||
              [group, freshness, component, quality].some(
                (v) => v !== "all",
              )) && (
              <button
                onClick={() => {
                  setSearch("");
                  setGroup("all");
                  setFreshness("all");
                  setComponent("all");
                  setQuality("all");
                }}
              >
                Clear filters
              </button>
            )}
        </div>
        <div className="table-scroll">
          <table>
            <caption className="sr-only">
              All registered measurements and their quality, freshness,
              observation times, sources, and history.
            </caption>
            <thead>
              <tr>
                <th>Measurement / component</th>
                <th>Last value</th>
                <th>Quality / freshness</th>
                <th>Observed / received / age</th>
                {!recorded && <th>Source / cadence</th>}
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {[...new Set(shown.map((i) => signalGroup(i.signal_id)))].map(
                (g) => (
                  <Fragment key={g}>
                    <tr className={`group-row ${groupTone(g)}`}>
                      <th colSpan={recorded ? 5 : 6}>
                        {g}{" "}
                        <span>
                          {
                            shown.filter((i) => signalGroup(i.signal_id) === g)
                              .length
                          }
                        </span>
                      </th>
                    </tr>
                    {shown
                      .filter((i) => signalGroup(i.signal_id) === g)
                      .map((i) => (
                        <tr
                          key={i.signal_id}
                          data-signal={i.signal_id}
                          data-event={i.observation?.event_id}
                          data-run={snap.run_id}
                          data-asset={snap.asset_id}
                          data-source={i.source_id}
                        >
                          <td>
                            <strong>{measurementName(i.signal_id)}</strong>
                            <span>{i.definition.component_id}</span>
                          </td>
                          <td
                            className={
                              state(i) === "stale" ? "stale-value" : "number"
                            }
                          >
                            {value(i.observation?.sample.value)}{" "}
                            <small
                              title={
                                i.definition.unit === "1"
                                  ? "Unitless fraction"
                                  : undefined
                              }
                            >
                              {i.definition.unit}
                            </small>
                          </td>
                          <td>
                            <Badge text={i.quality} />{" "}
                            <Badge
                              text={
                                recorded && state(i) === "fresh"
                                  ? "recorded"
                                  : state(i)
                              }
                            />
                          </td>
                          <td>
                            {recorded
                              ? i.observation
                                ? `${value(i.observation.sim_time_s)} working s`
                                : "Not observed yet"
                              : time(i.observation?.observed_at)}
                            <span>
                              {recorded
                                ? ""
                                : `Received ${time(i.observation?.received_at)}`}
                            </span>
                            <span>
                              {i.age_s === null
                                ? "No reading yet"
                                : `${value(i.age_s)} s old`}
                            </span>
                          </td>
                          {!recorded && (
                            <td>
                              {i.source_id}
                              <span>
                                Every {value(i.definition.sample_period_s)} s ·{" "}
                                {i.definition.classification}
                              </span>
                            </td>
                          )}
                          <td>
                            <button
                              className="text-button"
                              aria-label={`History for ${i.signal_id}`}
                              onClick={() => {
                                openHistory(i.signal_id);
                              }}
                            >
                              History ↗
                            </button>
                            <details>
                              <summary>Metadata</summary>
                              <div className="metadata">
                                <p>{i.definition.physical_location}</p>
                                {recorded && (
                                  <p>
                                    Source: {i.source_id} · Cadence:{" "}
                                    {value(i.definition.sample_period_s)} s
                                  </p>
                                )}
                                <p>
                                  Stored value{" "}
                                  {i.observation?.sample.value ?? "Unavailable"}{" "}
                                  {i.definition.unit}
                                </p>
                                {i.observation?.sample.raw_value !==
                                  undefined && (
                                  <p>
                                    Diagnostic raw value{" "}
                                    {i.observation.sample.raw_value}
                                  </p>
                                )}
                                <p>
                                  Provenance:{" "}
                                  {i.observation?.sample.provenance ??
                                    i.definition.provenance}
                                </p>
                                <p>
                                  Freshness deadline: {i.definition.freshness_s}{" "}
                                  s
                                </p>
                                <p>
                                  Model:{" "}
                                  {i.definition.model_version ?? "Not declared"}
                                </p>
                                <p>{i.definition.evidence_class}</p>
                                <p>
                                  Reason:{" "}
                                  {i.observation?.sample.reason ??
                                    "None declared"}
                                </p>
                              </div>
                            </details>
                          </td>
                        </tr>
                      ))}
                  </Fragment>
                ),
              )}
            </tbody>
          </table>
          {!shown.length && (
            <p className="empty">No measurements match these filters.</p>
          )}
        </div>
        <div className="table-foot">
          Showing {shown.length} of {snap.items.length} registered measurements.
        </div>
      </section>
    </>
  );
}
