# Energy advisory readiness

This page records the initial review before the missing features and browser cases were implemented. For current status, see the [final Phase 2 closeout](energy-advisory-acceptance.md).

Review date: November 4, 2026. **Evidence integrity passed; Phase 2 is not ready to close. Model/product promotion is withheld.** No Phase 3 or automatic-control authority is enabled.

The review checked the original six work items, six acceptance criteria, and five deliverables against the full milestone. The reviewed machine-readable checklist is [historical checklist 1.0.0](../../qualification/energy/readiness-checklist-1.0.0.json); the measured review is [energy-readiness-review.json](energy-readiness-review.json). Earlier passed and failed reports remain unchanged.

## What passed, and what remains

### Original work

| Original work item | Status | Evidence and precise limit |
|---|---|---|
| 1. Document energy ownership, equations, units and assumptions | Passed | Numerical/advisory/candidate model cards and ADR-0039/0040/0042; the simulator owns actual energy and the estimator uses declared parameters |
| 2. Build reference and route forecasts with immutable records | Passed within scope | A2 arithmetic, B2/C1 frozen template and D1 database/API/browser evidence; known simulated route at 120 simulation seconds |
| 3. Produce final charge, energy, time/range, reserve and station reachability | Partial | Route energy/charge/reserve exist. Conditional time/range exist in the saved reference output. No station-specific assessment or validated road approach exists; display needs completion |
| 4. Explicit uncertainty and measured coverage | Passed within scope | Frozen empirical scenario method; six of six reserved cases covered within unchanged width limits. This is not a probability/population guarantee |
| 5. Designated train/validation/held-out scenarios and fitting | Passed within scope | Twelve training, six validation and six held-out complete synthetic realizations; deliberate load, initial-charge, auxiliary/cold, stop/driver and capacity-mismatch regimes. No unseen-route or interacting-traffic claim |
| 6. Display age, horizon, versions, assumptions, uncertainty and explanations | Partial | Values, bounds, versions and traceability are displayed. Explicit input age, forward horizon, concrete assumptions and conditional driving time/range remain incomplete |

### Original acceptance criteria

| Criterion | Status | Evidence and precise limit |
|---|---|---|
| P2-01 numerical consistency | Passed | Nine analytical profiles against current numerical model 1.1.0; existing unit checks and unchanged 0.2/0.1-second fixed-plan refinement. Version 1.0.0's original report is historical |
| P2-02 private-state denial | Passed for the selected worker boundary | B1 actual seven denied file paths/four denied endpoints, unchanged worker sources/policy, C1 packaged-byte checks, D1 restricted input credential and ten database denial/immutability checks. Host/controller/administrator remain trusted; no claim of denying a malicious host or all inference from permitted measurements |
| P2-03 independent accuracy report and frozen limits | Passed within the frozen domain | Six reserved whole-mission forecasts, all available, every frozen criterion met; energy mean error 0.2850 kWh versus reference 2.3840 kWh, charge mean error 0.1790 points. The endpoint is last sampled mission state, not exact charger arrival; station accuracy is not established |
| P2-04 unavailable/degraded behavior including UI | Partial evidence | Missing/bad/scope/domain inputs have unit/API checks. Actual browser checks prove staleness, disconnect suppression, saved history and asset switching. Separate explicit missing-required-input and unsupported-domain Energy browser assertions remain required |
| P2-05 repeatability | Passed within scope | Same-input calculation/record checks, six exact validation-preparation comparisons and candidate/reference real-worker output comparisons; original inputs/configuration remain necessary for reproduction |
| P2-06 lineage and separation from observations | Passed | Immutable derived records carry event/wire, job, assignment, model and uncertainty identity. D1 verifies unchanged admitted packet bytes and exact fresh-cluster data/schema/permission restoration |

### Deliverables

| Deliverable | Status |
|---|---|
| Energy model cards | Present; current versus historical scopes clarified in this review |
| Calibration/validation and independent final report | Present, sealed and preserved |
| Dataset manifests | Present; all 24 primary missions and repeatability files verified |
| Forecast read API | Present for the documented route/cutoff; station-service work remains open |
| Private Phase 2 evidence | Present and verified; having evidence files alone does not complete missing work |

## Remaining work and closure tests

These are unfinished original Phase 2 requirements. They are not moved to Phase 6 and do not invalidate the scoped model/product passes already recorded.

| Part | Work | Required closure evidence |
|---|---|---|
| P2-E1a — explanations and time/range | Complete the forecast record/read view and Energy page with latest-input wall-clock age, forward simulation-time/distance horizon, declared capacity/reserve/equipment/motion assumptions, and conditional recent-consumption distance/time to reserve | Actual stored-input lineage; explicit seconds/meters or readable converted units; good zero distinct from missing; unavailable and expired values hidden; historical fields clearly historical; strict response validation and small actual-browser checks |
| P2-E1b — read-only station assessment | Bind each station to an explicit valid road approach and an assumed forward path. Assess energy and minimum reserve throughout that path. Include station/path/input/model lineage and explicit unavailable reasons | Reachable, reserve shortfall, already-at-station, station behind the vehicle, inaccessible/unmapped approach, wrong route, stale/missing data and intermediate reserve-breach tests. Reuse numerical/worker boundaries; no automatic station selection, command or charging action |
| P2-E1c — remaining product evidence and final review | Add separate missing-required-input and unsupported-domain Energy browser cases and recovery. Requalify affected storage/API/UI behavior after the preceding changes stabilize; rerun this readiness review | Current source-bound evidence, unchanged frozen model/targets/outcomes, privacy and cleanup, compatibility of old records/backups, explicit resolution of each remaining item; one final shared check after stabilization |

A station's GPS coordinates do not prove there is a bus-accessible road to it. The existing two hypothetical stations explicitly say their road access is not validated. Straight-line distance, a fraction of route length or the current end-of-route forecast must not be relabeled as station reachability. Mapping a read-only approach is a Phase 2 prerequisite; charger behavior, optimization and actuation keep their later-phase scopes.

Conditional distance/time means **assuming the recent driving pattern continues**, using independently declared capacity and the stated reserve. It is not a separately validated traffic prediction or a guarantee of driving until an empty battery. A forward horizon means the duration/distance covered by the assumed future plan; the current time of 120 seconds is not that horizon. Wall-clock input age and simulation forecast time must remain separate.

Add metadata without rewriting existing immutable records. Decide explicitly whether additive optional fields are compatible or a new composite record/view version and migration are needed. Preserve the exact numerical worker, model parameters, frozen targets and consumed held-out claim. Functional station tests can establish the assumed-plan calculation and unavailable states; they cannot inherit end-of-route accuracy numbers for new endpoints. A broader accuracy/operating-domain claim requires a separately documented protocol and independent evaluation, without retuning on or regenerating the consumed final corpus.

No unpassed item is waived. The original host-capacity/soak, comprehensive visual, broader reboot/deployment/storage-lifecycle, retention deletion and production-hardening gates remain Phase 6; the new gaps above do not belong to those deferrals. No silent corruption or unauthorized-control defect was identified by this review or deferred.

## Evidence compatibility

The audit verified nine current A2 source/input files, seventeen B1 boundary files, twelve B2 reviewed files, seven C1 reviewed files, 129 current D1 files and four files for the current nine-profile numerical regression. Frozen B2 manifest/model/targets/protocol/analysis references and the C1 evaluation artifacts, metrics and consumed claim match. The original 1.0.0 numerical report is retained; current numerical acceptance uses its qualified 1.1.0 successor.

All primary collection inventories match: **288 files / 221,108,979 bytes**, plus twelve repeatability files. Counts remain 12 training / 6 validation / 6 held out. No outcome was regenerated, rescored or used for fitting during this review.

Collection reports bind an older broad source inventory. They have 115 and 118 exact recorded source/input matches respectively, plus six reviewed D1 differences: the monitoring Dockerfile, API, connection-budget module, reader authentication, observation repository grants and recovery role bootstrap. The simulator/collector algorithms remain unchanged. The Dockerfile's added copy belongs to the application runtime stage; the collector uses the simulator stage. The current changed files must match their D1 requalification, and every original recorded collection file must still match its digest. This is explicit compatibility review, not deletion of a source guard or regeneration under newer application code.

The full D1 integration report retains its two reviewed later differences: CSS and the focused presentation-check controller. All 129 current presentation-report files match. The original full database/backup pass remains separate from its later interface-only check. Historical Phase 1 capacity evidence is not a current capacity qualification for forecasts.

## How the original review was run

In Windows PowerShell, in `<checkout>`, run:

```powershell
python scripts/known_route_energy/review_energy_advisory.py --evidence-dir evidence/p2/YOUR-NEW-READINESS-REVIEW
```

This historical command now audits the current advisory release. The original private report retains the following historical result. Choose an unused private evidence directory. This is a file/evidence audit; it does not launch services, regenerate data, run the fitter, score outcomes, resume a goal or change model authority. It streams file fingerprints to limit memory use. Normal completion takes seconds on the existing local files and prints three separate results:

- `Review completed: True`: the audit ran to completion.
- `evidence integrity: True`: the retained files and allowed compatibility bindings match.
- `Phase 2 ready: False`: unresolved original requirements remain. This was the expected historical result. The current release audit instead requires `Phase 2 ready: True`.

Exit code zero means the audit completed with valid evidence; it never means Phase 2 is ready by itself. Read the readiness flag and unresolved-item list in `review.json`. Missing, altered or incompatible evidence produces a failed audit and a nonzero exit. The reviewed checklist refuses missing/duplicate original items, unapproved deferred statuses, hidden/unreferenced gaps and control/scope changes. Future closure requires real implementation/evidence and an explicit reviewed checklist update; changing a checkbox alone is not proof of behavior. Even a future readiness pass does not automatically change runtime model labels or authorize control.

The audit tool has seventeen focused tests. Final shared checks passed 765 Python / 25 dashboard tests, fifteen strict modules and all 361 source fingerprints, with unchanged 339 original plus 81 operational contract outcomes per language. Formatting, type/build and candidate privacy/secret checks passed. These verify the review changes and preserve the current product evidence; they do not close the three service/UI gaps or repeat an application workload.
