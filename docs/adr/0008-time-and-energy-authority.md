# ADR-0008: one simulation clock and one energy authority

Date: November 3, 2026. Status: initial D-09 design selected; numerical/physical validation remains a P2 gate.

An authority is the component allowed to update state. If SUMO and a Python equation both subtract traction energy from the same battery, energy is lost twice. This decision prevents that ambiguity.

## Initial design and step order

One Python driver advances SUMO through TraCI (Traffic Control Interface), its programmatic control connection. Initial fixed step: 0.2 simulated seconds, matching the movement fixture. Simulator time defines causal order; independent wall timers cannot advance it.

SUMO owns movement and initial usable battery energy. Configure/test its battery mechanism explicitly in P2; the current movement probe does not validate it. Twin estimates remain separate derived values and never overwrite plant truth. Python owns sampling/fault injection, transport emulation, orchestration and later bounded thermal state. Avoid double-counting auxiliary losses.

P1 implementation update: [ADR-0011](0011-initial-simulated-plant.md) enables the minimal battery mechanism and dependent thermal observations in P1-B1, as required for a coherent monitoring plant. Numerical consistency and repeatability are separate from P2 physical calibration, convergence, depletion/charging and forecast validation. SUMO remains the single battery-energy authority.

1. At simulation time t, check queued commands against the completed state and local constraints.
2. Apply eligible changes for t to t + delta; advance SUMO once.
3. Read completed motion/energy; update dependent thermal state using a documented held-input scheme. Thermal limits affect the next interval.
4. Generate observations with sampling/noise/quality. Hidden truth goes only to the harness.
5. Record step size, versions, parameters, seed and input hashes. Abort/invalidate failures; do not silently change step size mid-run.

Commands record requested/applied/effect times and identity. Faster-than-real-time experiments do not establish real network latency.

## Time and units

Simulation time measures modeled behavior. UTC (Coordinated Universal Time) audits real observation/receipt/persistence time. A monotonic clock measures elapsed real duration without jumps from wall-clock adjustment. Store them separately; never subtract simulation time from UTC to claim latency.

Use explicit units at boundaries: meter, second, kelvin, watt, joule and SOC fraction 0–1. SUMO battery output includes watt-hours (Wh); 1 Wh = 3,600 J. Preserve original-unit provenance and test conversions. Energy API fields have rate/step-specific semantics; each selected field needs a documented version-specific conversion test. [Official SUMO electric model/output units](https://sumo.dlr.de/docs/Models/Electric.html)

P1 ordering uses source/session/sequence and observation time, not receipt time alone. Late history must not regress current state. Future skew and source conflicts require explicit rules. Stale, unknown, invalid and valid zero remain distinct.

## Validation and replacement

Before P2 claims, compare 0.1, 0.2 and 0.4-second steps on fixed missions; freeze convergence/error tolerances before held-out evaluation. Test traction/auxiliary/regenerative/charging balance, capacity bounds, stopped behavior, depletion, charging, conversion and stability. Choosing an authority does not validate its physical accuracy.

A custom energy model must replace the authority, disable competing accounting, document integrator/coupling order, and rerun conservation/convergence/causality checks. Physical hardware has its own clock/calibration/protection boundary.
