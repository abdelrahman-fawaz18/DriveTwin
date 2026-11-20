# ADR 0059. Freeze station forecasts before the reserved drive study

Use a distance baseline and a route-aware estimate of arrival and minimum battery energy. Calibrate only the distance-proportional propulsion term and interval widths on the six development drives. The candidate uses static road speed and signal geometry to estimate travel time. The forecast receives explicit departure inputs and expected wait. The reserved scorer records immutable predictions before starting the simulator.

Set energy, charge, optimism, range coverage, range width, and reserve classification limits before reserved collection. A design-level claim prevents using the same reserved cases with a changed forecast release. Preserve every outcome and require all six complete cases for qualification. Keep the Phase 2 evidence as a separate known-route study. [Forecast and evaluation guide](../setup/station-arrival-forecast.md) · [Release](../../qualification/charging/station-arrival-release-1.0.0.json)

Use a passing release as the energy input for Phase 4B offline station eligibility. Phase 4B must still check connector, directed access and mission rejoin, status freshness and occupancy, expected waiting, and decision explanations. Runtime model qualification and automatic dispatch approval remain open.
