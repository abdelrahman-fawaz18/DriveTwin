# Energy advisory model card

Models: `recent-net-distance-baseline/1.0.0` and `observed-plan-advisory/1.0.0`. The supplied-plan method uses numerical model `longitudinal-energy-estimate/1.1.0`.

Historical model scope: generic advisory calculation methods without built-in vehicle calibration or control authority. The [synthetic fitted candidate](energy-calibrated-candidate-card.md), persistence and scoped display passed the [final advisory closeout](../verification/energy-advisory-acceptance.md). The earlier [readiness review](../verification/energy-advisory-readiness.md) records its then-pending work. Generic worker labels and frozen studies retain their identities. Current geographic forecasts use the separate [route-aware model](route-aware-energy.md).

## What each method answers

| Method | Question | Assumptions and limits |
|---|---|---|
| Recent-consumption reference | If the recent driving pattern continues, how much energy would a declared remaining distance use? | Positive net consumption over enough observed distance/time; no external charging; conditional range/time only |
| Supplied-plan estimate | Given an assumed speed/grade/equipment schedule, what charge and reserve would remain? | Independently declared vehicle parameters and capacity; continuous motion beginning at observed speed; no knowledge of actual future motion |

The recent-consumption method is the comparison reference for the calibrated candidate. The reserved-trip evaluation reports their errors on the same six unseen missions. [Evaluation result](../verification/energy-heldout.json)

## Inputs and validity

Both methods read good-quality coincident battery energy, cumulative distance, charge fraction and forward speed from already admitted simulation observations. Configurable signal bindings support different identifiers while preserving required physical units. All observations must belong to one asset/run/source/session/epoch and use increasing time and sequence. Accepted provenance, maximum gaps/window/age and work budgets are explicit policy values. Missing/bad/duplicate required values, stale/future time, mixed identity, odometer regression and unsupported mode/clock produce unavailable output. Replay mode and physical inputs are not supported by this initial selector.

A recorded analysis has a frozen simulation analysis time. The persistent product input view supplies trusted running-clock and connection information through authenticated access. Merely passing `source_state="running"` is not proof that a real connection is healthy. External charging must be explicitly excluded by the caller; its absence is not sensed or authenticated here.

Every request declares remaining distance, capacity assumption, mission/plan identifiers and reserve charge fraction. There is no automatic reserve default. All percentages in synthetic fixtures are illustrative. The initial energy estimate is declared capacity multiplied by reported charge; the calculation does not infer actual capacity from the simulator's energy/charge ratio.

## Outputs and accounting

Successful results carry estimated trip energy, final charge, minimum estimated energy, unserved energy, assumed energy-plan feasibility and reserve feasibility. The reference additionally reports recent kWh/km and kW and conditional distance/time to reserve. Net recent consumption includes regeneration and equipment once. Planned calculations do not manufacture a range beyond the supplied plan.

Reserve feasibility checks the entire numerical trajectory, including its start. A later charge recovery cannot erase an earlier reserve breach or energy shortfall. The model does not prove achievable motion, safe braking or authorized operation.

Result records are immutable ordinary application objects. They carry input references and normalized digests, input age/clock, request/assumption identifiers, policy identifier, model versions and numerical step. A deterministic forecast identity includes all calculation inputs and settings. Reproduction also requires retaining the corresponding inputs/configuration; hashes are fingerprints, not a backup. D1 now supplies durable composite storage and scoped read APIs; these generic calculation objects remain their unmodified historical format.

## Uncertainty and release scope

The generic calculation objects retain `model_qualified=false` and `uncertainty_method="not_established"`. The recent-consumption method assumes the observed movement pattern continues. The planned method uses the supplied schedule and parameters. Grade, load, traffic, equipment, and charging can change the actual outcome.

The separate calibrated candidate was fitted on twelve training trips, selected using six validation trips, and evaluated once on six reserved trips. Its product record carries an empirical scenario range, model lineage, and supported domain. The final advisory review includes storage, authorization, forecast explanations, station assessments, browser states, and restoration. [Candidate card](energy-calibrated-candidate-card.md) · [Advisory closeout](../verification/energy-advisory-acceptance.md)

The isolated worker receives selected observations and a stated plan. Source state, identity, freshness, and route assignment are checked by the authenticated input view before calculation and by current reads before display. External charging exclusion remains a declared configuration assumption. [Forecast setup](../setup/persistent-energy-forecasts.md)
