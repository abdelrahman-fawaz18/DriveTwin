import { Bars } from "../../ui/Chart.tsx";
import type { EnergyView } from "./energyModel.ts";
import { reasonText } from "./energyModel.ts";
import { value } from "../../monitoring/model.ts";

export function EnergyDetails({ data }: { data: EnergyView }) {
  const e = data.extensions?.explanation;
  const reference = data.reference_values;
  return (
    <>
      <p className="fine" data-testid="forecast-input-age">
        Latest required reading age:{" "}
        {data.input_age_s == null
          ? "unavailable"
          : `${value(data.input_age_s)} seconds`}
      </p>
      {e ? (
        <>
          <div className="cards energy-context-cards">
            <article className="card">
              <span>Assumed route forecast horizon</span>
              <strong data-testid="forecast-horizon">
                {value(e.horizon_duration_s / 60)} min ·{" "}
                {value(e.horizon_distance_m / 1000)} km
              </strong>
              <small>
                Forward plan duration and distance; not a traffic arrival-time
                guarantee.
              </small>
            </article>
            <article className="card">
              <span>
                Conditional distance to {value(e.reserve_soc * 100)}% reserve
              </span>
              <strong data-testid="conditional-range">
                {reference?.conditional_distance_to_reserve_m == null
                  ? "Unavailable"
                  : `${value(reference.conditional_distance_to_reserve_m / 1000)} km`}
              </strong>
              <small>If recent net consumption continues.</small>
            </article>
            <article className="card">
              <span>
                Conditional time to {value(e.reserve_soc * 100)}% reserve
              </span>
              <strong data-testid="conditional-time">
                {reference?.conditional_time_to_reserve_s == null
                  ? "Unavailable"
                  : `${value(reference.conditional_time_to_reserve_s / 60)} min`}
              </strong>
              <small>
                If the recent driving and equipment pattern continues; not time
                until an empty battery.
              </small>
            </article>
          </div>
          <details className="energy-assumptions" open>
            <summary>Prediction assumptions</summary>
            <p data-testid="forecast-assumptions">
              Declared battery capacity: {value(e.declared_capacity_kwh)} kWh ·
              Loaded mass: {value(e.loaded_mass_kg)} kg · Reserve:{" "}
              {value(e.reserve_soc * 100)}% · Equipment:{" "}
              {value(e.auxiliary_power_w / 1000)} kW
            </p>
            <p>
              {e.motion_assumption}. {e.grade_assumption}. {e.capacity_basis}.{" "}
              {e.external_charging_basis}.
            </p>
          </details>
        </>
      ) : (
        <p className="fine">
          Additional explanation is unavailable for this older record.
        </p>
      )}
      {data.extensions?.stations.items.some((s) => s.values) && (
        <div className="forecast-comparison">
          <h3>Station approach energy</h3>
          <Bars
            items={data.extensions.stations.items.map((s, n) => ({
              label: s.station_id,
              value: s.values?.net_mission_energy_kwh ?? null,
              color: ["#d9277f", "#8552ba", "#368e85"][n % 3]!,
            }))}
            unit="kWh"
          />
          <p className="energy-chart-note">
            Assumed approach plans. Availability and site access are unverified.
          </p>
        </div>
      )}
      <h3>Station reachability under stated assumptions</h3>
      <p className="fine">
        Charging bays with checked bus-road approaches. Site access and charger
        availability remain unverified. These station estimates have no
        independently measured arrival-accuracy or interval-coverage guarantee.
      </p>
      {!data.extensions?.stations.items.length ? (
        <p>Station navigation is not configured for this record.</p>
      ) : (
        <div className="table-scroll">
          <table aria-label="Station assessments">
            <thead>
              <tr>
                <th>Station</th>
                <th>Assessment</th>
                <th>Road path / assumed time</th>
                <th>Modeled energy / final charge</th>
                <th>Road approach</th>
              </tr>
            </thead>
            <tbody>
              {data.extensions.stations.items.map((s) => (
                <tr key={s.station_id}>
                  <td>
                    {s.station_id}
                    <br />
                    Availability unverified
                  </td>
                  <td>
                    {reasonText(s.status)}
                    <br />
                    {s.reason_codes.map(reasonText).join(", ")}
                  </td>
                  <td>
                    {s.path && s.values
                      ? `${value(s.path.remaining_distance_m / 1000)} km / ${value(s.path.horizon_duration_s / 60)} min`
                      : "Unavailable"}
                  </td>
                  <td>
                    {s.values
                      ? `${value(s.values.net_mission_energy_kwh)} kWh / ${value(s.values.projected_final_soc * 100)}%`
                      : "Unavailable"}
                    {s.uncertainty && (
                      <small className="station-scenario">
                        Scenario energy{" "}
                        {value(s.uncertainty.minimum_energy_kwh)}–
                        {value(s.uncertainty.maximum_energy_kwh)} kWh; station
                        coverage unmeasured
                      </small>
                    )}
                  </td>
                  <td>
                    {s.path ? (
                      <details>
                        <summary>Validated directed road path</summary>
                        <p>
                          {s.path.edges.length} road segments · Approach lane{" "}
                          {s.path.approach_lane_id}
                          <br />
                          {s.path.motion_assumption}
                          <br />
                          Physical site access: unverified
                        </p>
                      </details>
                    ) : (
                      "Unavailable"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
