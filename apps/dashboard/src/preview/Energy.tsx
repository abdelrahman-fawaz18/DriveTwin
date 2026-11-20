import { useState } from "react";
import { Bars, Chart } from "../ui/Chart.tsx";
import { palette, number, sampleValue } from "./data.ts";
import { Details, Empty, isStale, Metric, Panel, Status } from "./ui.tsx";
import type { PreviewContext } from "./ui.tsx";

export function Energy({ c }: { c: PreviewContext }) {
  const [record, setRecord] = useState("Latest"),
    [station, setStation] = useState<string | null>(null);
  const unavailable =
    record === "Latest" &&
    (isStale(c) || c.mode === "Missing data" || c.mode === "Out of domain");
  const start = record === "Latest" ? 120 : Number(record),
    soc = sampleValue("battery.pack.soc", start, c.vehicle) * 100;
  const points = Array.from({ length: 61 }, (_, i) => ({
    t: start + i * 10,
    value: soc - (((i / 60) * 12.8) / 300) * 100,
  }));
  return (
    <>
      <div className="energy-toolbar">
        <Status label="Advisory" tone="violet" />
        <span className="muted">
          Input age {number(c.age, 1)} s · Working horizon 600 s
        </span>
        <label className="push-right">
          Record
          <select
            aria-label="Forecast record"
            value={record}
            onChange={(e) => setRecord(e.target.value)}
          >
            <option>Latest</option>
            <option value="90">Saved at 90 s</option>
            <option value="60">Saved at 60 s</option>
          </select>
        </label>
      </div>
      {unavailable ? (
        <Panel>
          <Empty
            title="Current forecast unavailable"
            text={
              c.mode === "Out of domain"
                ? "The current input is outside the model's evaluated operating range."
                : c.mode === "Missing data"
                  ? "Required energy readings are missing. Saved forecast records remain available."
                  : "The current readings are stale. A fresh input snapshot is required."
            }
          />
        </Panel>
      ) : (
        <>
          <div className="metrics-row three">
            <Metric
              label="Forecast use"
              value={12.8}
              unit="kWh"
              tone="pink"
              icon="Energy"
              caption="Assumed remaining route"
            />
            <Metric
              label="Arrival charge"
              value={soc - 12.8 / 3}
              unit="%"
              tone="violet"
              icon="battery"
              caption="Projected at the route endpoint"
            />
            <Metric
              label="Reserve"
              value={15}
              unit="%"
              tone="charcoal"
              icon="shield"
              caption="Policy minimum charge"
            />
          </div>
          <div className="energy-grid">
            <div className="stack">
              <Panel title="Route forecast">
                <div className="chart-legend">
                  <span>
                    <i style={{ background: palette.pink }} />
                    Forecast
                  </span>
                  <span>
                    <i style={{ background: "#bca1e2" }} />
                    Scenario range
                  </span>
                  <span>
                    <i style={{ background: palette.amber }} />
                    Reserve
                  </span>
                </div>
                <Chart
                  points={points}
                  color={palette.pink}
                  unit="% charge"
                  label="Projected charge with scenario range and reserve"
                  domain={[0, 100]}
                  reserve={15}
                  band={2.4}
                  height={275}
                />
              </Panel>
              <Panel title="Station approach energy">
                <Bars
                  unit="kWh"
                  items={[
                    { label: "Station A", value: 2.4, color: palette.cyan },
                    { label: "Station B", value: 3.6, color: palette.violet },
                  ]}
                  maximum={4}
                />
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Charging bay</th>
                        <th>Road access</th>
                        <th>Energy feasible</th>
                        <th>Arrival charge</th>
                      </tr>
                    </thead>
                    <tbody>
                      {["A", "B"].map((s, i) => (
                        <tr key={s}>
                          <td>
                            <button
                              className="text-button"
                              onClick={() =>
                                setStation(station === s ? null : s)
                              }
                            >
                              Station {s}
                              <span>↗</span>
                            </button>
                          </td>
                          <td>Available</td>
                          <td>
                            <Status label="Yes" />
                          </td>
                          <td>{number(soc - (i ? 3.6 : 2.4) / 3, 1)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {station && (
                  <div className="station-detail">
                    <strong>Station {station} · Assumed approach</strong>
                    <dl className="key-values">
                      <dt>Road distance</dt>
                      <dd>{station === "A" ? "2.1" : "3.2"} km</dd>
                      <dt>Assumed approach time</dt>
                      <dd>{station === "A" ? "6" : "9"} min</dd>
                      <dt>Energy scenario range</dt>
                      <dd>{station === "A" ? "2.1–2.7" : "3.1–4.1"} kWh</dd>
                      <dt>Charger availability</dt>
                      <dd>Unverified</dd>
                      <dt>Road approach</dt>
                      <dd>Bus-accessible path</dd>
                    </dl>
                  </div>
                )}
              </Panel>
            </div>
            <aside className="stack">
              <Panel title="Assumptions">
                <dl className="key-values">
                  <dt>Capacity</dt>
                  <dd>300 kWh</dd>
                  <dt>Loaded mass</dt>
                  <dd>15,000 kg</dd>
                  <dt>Motion</dt>
                  <dd>Assumed plan</dd>
                  <dt>Grade</dt>
                  <dd>Flat</dd>
                  <dt>Equipment</dt>
                  <dd>4.3 kW</dd>
                  <dt>Horizon</dt>
                  <dd>600 s</dd>
                  <dt>Charging</dt>
                  <dd>Excluded from plan</dd>
                </dl>
              </Panel>
              <Panel title="At recent consumption" className="tinted teal">
                <div className="estimate">
                  <span>Distance to reserve</span>
                  <strong>
                    86 <small>km</small>
                  </strong>
                </div>
                <div className="estimate">
                  <span>Time to reserve</span>
                  <strong>
                    2.5 <small>h</small>
                  </strong>
                </div>
                <p className="fine">
                  Conditional on the recent driving and equipment pattern
                  continuing.
                </p>
              </Panel>
              <Panel title="Forecast context" className="gradient charcoal">
                <p>Known route · fixed model</p>
                <p className="fine">
                  Original readings stay available in Measurements. The shaded
                  band shows the modeled scenario range.
                </p>
              </Panel>
            </aside>
          </div>
        </>
      )}
      <Details title="Forecast records">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Input working time</th>
                <th>Model</th>
                <th>Forecast use</th>
                <th>State</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {[120, 90, 60].map((t) => (
                <tr key={t}>
                  <td>{t} s</td>
                  <td>Energy model · sample v1</td>
                  <td>12.8 kWh</td>
                  <td>Saved</td>
                  <td>
                    <button
                      className="text-button"
                      onClick={() =>
                        setRecord(t === 120 ? "Latest" : String(t))
                      }
                    >
                      View record
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Details>
      <Details title="Model and input details">
        <dl className="metadata-grid">
          <dt>Input identity</dt>
          <dd>{c.vehicle} · preview recording</dd>
          <dt>Observed inputs</dt>
          <dd>Battery energy, charge, speed, equipment load</dd>
          <dt>Model release</dt>
          <dd>Illustrative fixed-model response</dd>
          <dt>Provenance</dt>
          <dd>Deterministic preview sample</dd>
          <dt>Reference assumptions</dt>
          <dd>Known route, declared capacity and mass</dd>
          <dt>Scenario bounds</dt>
          <dd>Parameter sensitivity envelope</dd>
        </dl>
      </Details>
    </>
  );
}
