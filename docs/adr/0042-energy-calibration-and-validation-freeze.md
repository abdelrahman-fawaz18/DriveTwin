# ADR-0042: Synthetic energy calibration and validation freeze

Date: November 4, 2026. Status: selected for P2-B2. Measured results and exact frozen artifact references belong in the verification/release records.

## Collection and data ownership

Collect only the 12 training and 6 validation assignments in the unchanged 24-mission design. Do not generate or score the six held-out outcomes in P2-B2. The design remains a plan; a separate accepted inventory records which missions actually exist. Derived copies stay with their causal family.

Reuse the locked SUMO 1.28.0 source, plant, contract validator and Montreal route, with generated private scenario copies. Existing monitoring code/configuration is unchanged. Apply each recipe's initial charge, actual capacity, loaded mass, auxiliary demand, ambient temperature, seed and stop duration to those copies. Set nonzero driver imperfection to make the seed affect speed variation: 0.35 for urban and 0.6 for the stop-heavy proxy. Stop-heavy means a longer scheduled dwell and stronger driver variation on this route, not interacting fleet traffic. Cold conditions change ambient thermal input and declared equipment demand; no new battery chemistry/temperature-efficiency model is claimed. [SUMO reproducible randomness](https://sumo.dlr.de/docs/Simulation/Randomness.html), [electric model](https://sumo.dlr.de/docs/Models/Electric.html).

Use the existing sensor adapter to add declared Gaussian energy noise (7,200 J standard deviation), 3,600-J energy quantization, charge noise (0.0005 fraction), 0.0005 charge quantization and a per-realization +/-0.002 charge bias. Noise/seed/bias are synthetic hypotheses, not measured sensor specifications. SUMO's actual battery state remains unchanged. Every original 20-signal packet is admitted by the existing 3.0.0 contract and saved canonically. No broker/database delivery claim is inferred from offline admission.

Require complete mission completion, independent causal trace/content hashes, exact recorded-file inventories and one exact repeated training mission. Content hashes omit run/event identities, filenames and wall time. Run one bus at a time with a 512-MiB container limit; this is accelerated sequential collection, not a live capacity/soak benchmark. Preserve raw packets, physical truth traces, source manifests and generated scenarios privately. Later estimator additions do not regenerate accepted data; verify the recorded collection files and source/configuration guards before reuse.

## Training fit

Fit effective rolling resistance and propulsion efficiency on training observations only. Capacity stays independently declared at 350 kWh, mass at 18,000 kg and other estimator geometry/loss assumptions remain explicit. Do not infer actual capacity from ideal energy/charge ratios or read recipe truth parameters into the estimator. Actual high-load and reduced-capacity recipes therefore exercise mismatch.

Reconstruct historical training motion from consecutive one-second speed observations with flat-grade assumptions and observed equipment power. This uses completed training history for fitting, not an operational future prediction. Score a fixed 6-by-6 parameter grid using equal whole-trip mean squared net-energy error. Compute resistance-dependent traction/recovery features once per resistance candidate, then scale positive traction work by efficiency. Verify the selected reduction against the complete bounded numerical model, including clipping/shortfall refusal. Keep all candidate scores and leave-one-trip-out selections.

A fitted value is an effective approximation for this model/data, not an identified physical tire or drivetrain specification. Several resistance/efficiency pairs can explain nearly the same trip energy. Report that tradeoff and leave-one-trip-out variability as properties of the synthetic fit. No new fitting framework or runtime dependency is introduced.

## Causal forward plan and evaluation

Select the complete training trip nearest the median training duration, with a stable ID tie break. Retain only its time/distance/speed profile as a frozen historical motion template. Estimate the known route's terminal odometer from the median training endpoint. All regimes share this training-only template; the validation recipe label, actual capacity, seed and future traffic do not choose it.

At 120 simulation seconds, use the most recent 60 seconds of admitted observations. Anchor the historical template at current odometer/speed, retain at most 900 motion intervals and rescale durations to the declared remaining distance. Hold the latest good observed auxiliary demand constant. This is an explicitly coarsened/re-timed forward assumption learned from other trips, not the validation trip's actual future movement. Projection/clock/scope/quality guards remain unchanged. The reference baseline uses the same prefix, capacity, remaining distance and reserve assumption.

Prepare and save the entire prediction batch before reading physical truth rows for scoring. Reproduce all six candidate and six baseline jobs through the qualified restricted worker. Its image is rebuilt from the same pinned inputs, compared with guarded packaged bytes and inspected under the existing policy; do not rely on an unavailable historical local image ID. The fitter/controller is trusted harness code; the operational worker receives only minimized jobs and cannot import fitting/data modules.

Score one independent case per complete mission. The endpoint is the last sampled state before exit in a successfully completed mission; do not call it an exact charging-station arrival. Energy truth is physical battery change from cutoff to that endpoint, while charge truth is the physical endpoint fraction. Preserve counts, unavailable/failed forecasts, signed errors, per-regime results and baseline comparison. Do not impute failed predictions or silently discard them. Step refinement compares the identical assumed forward plans at 0.2/0.1 seconds; it is not a changed-SUMO-driver experiment or physical accuracy proof.

## Uncertainty selection and preserved failure

The first validation method varied resistance and efficiency independently, plus explicit mass, capacity, equipment, recovery and charge/stop assumptions. Its point errors and coverage passed, but its energy/charge ranges exceeded the unchanged width limits. Preserve that rejected result.

The selected successor retains joint grid pairs with training MSE within 10% plus 0.0025 kWh squared of the best fit, plus every leave-one-trip-out pair. This preserves the empirically observed resistance/efficiency tradeoff in the selected combinations. It was selected during validation, before held-out outcomes. Mass/capacity (+/-20%), recovery (+/-0.1), equipment (+/-2 kW), charge (+/-0.005) and extra stop equipment-time (120 s) assumptions stay unchanged, as do all quality thresholds. The original collection protocol is not rewritten; the successor has a separate versioned selection policy.

Evaluate sampled parameter scenarios and the explicit equipment/stop allowance. `training-joint-grid-envelope/1.0.0` is an empirical sensitivity envelope, not a rigorous global bound, confidence region or probability guarantee. Report observed joint coverage and width; six deliberately varied validation missions cannot establish population coverage. Keep the candidate advisory until independent held-out review and later product gates pass.

## D-18 scoped freeze

Freeze the original advisory error budget after validation review, without loosening it to pass: all six forecasts and baselines available; energy mean absolute error <=1 kWh and maximum <=2 kWh; final charge mean absolute error <=0.5 percentage points and maximum <=1 point; candidate energy MAE no worse than the reference; empirical joint envelope coverage >=5/6 complete missions; maximum widths <=6 kWh / 4 charge points; fixed-plan energy refinement change <=1%.

The mean/maximum energy limits form a 1/2-kWh short-trip advisory budget. A maximum 1-point charge error is 3.5 kWh at the declared capacity, one tenth of the illustrative 35-kWh reserve. Width caps prevent trivial coverage from uninformative ranges. These are engineering acceptance choices for this synthetic advisory study, not externally sourced safety limits or approval for dispatch. The 10% reserve remains an experimental assumption. Thermal/maintenance/effect and real-vehicle thresholds stay open for their later validation gates.

Freeze model, targets and final evaluation protocol as separate artifacts, sealed by byte digests and a versioned reviewed release manifest. Check actual files, source/input compatibility, prediction/job binding and recorded metrics before release eligibility. Unit tests must reject changed model, targets, prepared job, prediction, source or held-out-state artifacts. Hash sealing detects changes; it does not remove the trusted administrator's ability to edit files.

The candidate remains `model_qualified=false`. P2-C1 must verify the actual frozen artifacts and successful validation before generating the six held-out missions, report every independent result against the unchanged targets and preserve failures. No P2-C1 work or automatic command is started by the P2-B2 release.
