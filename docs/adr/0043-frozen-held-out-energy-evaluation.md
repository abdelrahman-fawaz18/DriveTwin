# ADR-0043: Frozen held-out energy evaluation

Date: November 4, 2026. Status: selected for the reserved-trip evaluation. Measured results belong in the separate reviewed evaluation record; this decision does not assume a pass.

## Scope and frozen authority

Evaluate exactly the six reserved complete missions from the existing 24-mission design. Keep model parameters, historical template, joint parameter pairs, sensor/driver recipes, forecast cutoff/window, endpoint definition, metrics and D-18 limits unchanged from the reviewed B2 release. Do not fit, select another template, widen uncertainty, drop a regime or retune after seeing these outcomes. A failed result remains failed; any later tuning needs a separately documented independent evaluation decision.

Verify actual model, target, evaluation-protocol, analysis and source bytes against `docs/models/energy-validation-release.json` before collection. Require successful validation, training-only parameter/template provenance, the exact known protocol and the original advisory authority. Call the existing held-out purpose gate with actual verified artifact digests, not arbitrary identifiers. Preserve the B2 manifest as an immutable historical release; record C1 consumption/outcomes separately.

## Runner qualification before opening outcomes

New orchestration must reproduce all six sealed validation preparations first, inside the locked Linux Python runtime. Compare exact serialized candidate/reference jobs, scenario envelopes and fixed-plan refinement values with B2. The validation-only preflight must pass before reserving a held-out attempt. It does not use new outcomes or alter the frozen algorithms.

Use a release-fingerprint-bound one-time private claim to prevent an ordinary second command from regenerating another trial. The controller refuses an existing claim and offers read-only inspection. The claim records an incomplete attempt as well as a completed one. Preserve technical failures and partial artifacts; do not erase the marker or regenerate data to obtain a better score. Any recovery must retain original data and demonstrate compatibility of a harness-only correction, with no model/criterion changes.

## Collection and cross-partition integrity

Reuse the unchanged B2 collector function and scenario mapping after the release/claim gates. The development command continues to forbid held-out collection. A separate C1 command selects only the six held-out assignments, one bus at a time, with the existing 512-MiB restricted collection configuration. Generate no duplicate/repeatability trial of the reserved missions.

Combine the new six artifact assignments with the sealed 18-mission development inventory. Require all 24 original design missions, correct family/recipe/split ownership and no copied causal/observation content across partitions. Verify every original held-out file before preparation and scoring. This is accelerated sequential synthetic collection, not fleet throughput or a physical deployment test.

## Causal predictions and independent execution

The preparation function takes only frozen training artifacts and an observation prefix ending at the exact 120-second cutoff. Reject future observations, missing cutoff/current auxiliary demand and a changed historical template. Use the same latest-60-second window, fixed 350-kWh/18,000-kg estimator assumptions, historical plan anchoring, observed equipment hold and illustrative reserve as B2. No validation or held-out recipe label, actual capacity, seed or future movement chooses a forecast.

Keep fitting/template-selection calls outside this path. New preparation is checked against saved validation inputs and the frozen B2 source. Sensor annotations cannot change the numerical jobs. A serialized job fingerprint preserves numeric representation as well as values, avoiding accidental cross-runtime/clock-identity changes.

Run candidate and reference jobs through the existing isolated worker for all six missions. Rebuild from the same guarded pinned inputs, inspect actual restrictions and verify packaged bytes; exclude plant and fitting modules. Save all twelve responses and their input/output digests as a complete batch before physical outcomes become scoring inputs. The trusted harness may read bytes for integrity checks; that is distinct from supplying truth to the predictor. The worker has no raw project/data mount or external network.

## Scoring, failures and interpretation

Use the unchanged B2 summary function and actual frozen targets. Score one case per complete mission at its last sampled state before exit. Report energy errors in kWh and final charge errors in percentage points, signed per-regime values, availability/failure counts, candidate/reference comparison, joint scenario coverage, width caps and fixed-plan 0.2/0.1-second refinement. Add reference charge errors as descriptive metrics without changing acceptance rules.

Reject incomplete or altered job/worker batches before scoring. A valid unavailable forecast remains an explicit failed/unavailable case in the six-case denominator. Do not substitute zeros, discard failures or score fabricated worker values. Preserve an infrastructure failure separately from a completed evaluation that misses quality criteria.

This provides an independent test for these deliberately reserved realizations on a known synthetic route. Six cases do not establish population probability coverage, unseen-route performance, real-bus accuracy or safety. The scenario envelope remains empirical, not a confidence guarantee. Passing C1 does not mutate old worker labels or approve automated control: persistent calibrated/uncertainty records and product integration remain P2-D1, with final promotion reviewed in P2-E1.

## Evidence and cleanup

Raw packets/truth, private claim, scenario copies, job/worker responses, image logs and detailed results remain ignored. Version only independently authored code, reviewed aggregate results and sealed evaluation references. Verify original resource inventories and source/frozen-artifact compatibility after execution. Stop/remove only owned temporary containers; preserve all preexisting volumes/networks. No global agent, host memory, engine setting, publication or Phase 3 action is required.

Release manifests are byte-fingerprinted metadata. Git's normal source line-ending conversion would store LF bytes for the qualified Windows CRLF manifest and cause a false mismatch on checkout. Mark only `docs/models/*-release.json` as `-text` so Git preserves the actual qualified bytes; do not edit or reformat the frozen file. Verify staged blob fingerprints against the qualified working files before commit. This corrects metadata storage compatibility without changing model/target/protocol bytes, recalculating outcomes or relaxing any evidence guard. Ordinary source line-ending policy remains unchanged.
