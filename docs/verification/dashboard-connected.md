# Connected dashboard verification

The approved nine-tab presentation is integrated with the existing monitoring, history, map, energy, operator, and replay components. Monitoring, replay, populated energy forecasts, and enabled operator services passed their connected checks. The application runtime remains unchanged from revision `6038c11`. Each result covers its named service configuration; earlier release reports retain their recorded revisions and scopes.

| Check | Status and scope |
|---|---|
| Strict TypeScript | Passed for the connected application and preview |
| Frontend unit checks | 35 passed, including all twenty standard signals and the production import boundary |
| Production builds | Passed for monitoring/replay and the separate sample preview; the preview retains its existing large-bundle warning |
| External review preparation | Four focused policy and launcher checks passed; the committed-source check-only command passed without starting services |
| Privacy and source checks | Five privacy unit checks passed; candidate and staged privacy/Gitleaks scans reported zero findings |
| Windows/Ubuntu compatibility | The completed service run and current source inventory match; the qualified Linux launcher was used |
| Browser inspection | All nine tabs captured during the live drive; desktop and 390-pixel mobile navigation and map checks passed |
| Connected readings, history, map, and recovery | Passed for all twenty signals, 300 original messages, isolated replay, pause, source disconnect, and browser reconnect |
| Positive forecast and operator service regression | Passed: displayed forecast values, stale and disconnected states, and three Operator cases with actual services |

The external review captured 44 dashboard images, reconciled 300 messages and 6,000 readings, and verified replay isolation. Temporary containers, volumes, and networks were absent on independent inspection. The unchanged host guards passed. [Run procedure](../setup/dashboard-review.md)

The connected map uses actual paired telemetry coordinates and the original mission context. Source APIs, numerical models, stored observations, command permissions, and replay boundaries remain unchanged. Shared visual assets are imported independently of preview sample data. Historical model and workload evidence is preserved.

## Accepted monitoring and replay review

The operator, controller, service worker, source, and automated browser reports all passed. Their embedded reports agree. The source inventory recorded before and after execution matches the reviewed tree, and the dashboard review configuration is unchanged.

| Area | Verified result |
|---|---|
| Live readings | All 20 standard signals; browser values advanced as observations arrived |
| Accounting | 300 accepted original messages, 6,000 readings, and zero pending messages at completion |
| History and replay | All 20 signal histories checked; 300 original packets in isolated replay; recorded history and playback rendered |
| Freshness | Healthy pause beyond the freshness deadline, stale source disconnect, new readings after reconnection, and browser disconnect/resynchronization passed |
| Map | 1,158 map features; observed trajectory and planned route rendered; paused position and mobile navigation checked |
| Presentation | Three capture rounds across all nine tabs, plus recovery, history, replay, and mobile captures; all 44 listed PNG files present |
| Startup and resources | Services ready in 8.60 seconds with prepared images; recorded Windows available memory stayed at or above 3.999 GiB; host guards passed |
| Completion | Normal exit, unchanged source, temporary resources removed, and temporary sleep prevention released |

Visual inspection confirmed rounded overview cards, grouped Fleet measurements, the contained map legend and side controls, and the seven-stage delivery path. The System diagram reports observed service availability and communication status. Docker process inventory and per-container CPU or memory measurements are outside that display. Minor mobile polish remains: separate the latitude and longitude text and reduce street-label crowding at small widths.

Vehicle speed ranged from 0 to 11.11 m/s across the retained 300 samples. The 114 zero-speed samples are stationary intervals in the source recording. The moving Overview capture displays 39.996 km/h for 11.11 m/s.

The monitoring review profile starts without a forecast producer or command deployment. Its Energy and Operator captures therefore verify the unavailable states. The separate reviews below cover populated forecast and command-service behavior. This five-minute one-bus review establishes functional behavior within its stated scope; the earlier endurance and numerical model results retain their original scopes.

Source identity for this review:

- Application and runner inventory: 371 files, SHA-256 `b526b101a97cec74539a74e425a133892e93b3803d24a11b2f1598d55b41b7fe`.
- Release profile SHA-256: `559c8b241e55129d3039f4910733b9aca62a9c37f12059967d4c85b46a3a4d5c`.
- Tested source revision: `6038c116b7bc2bdc3094771b5b6853d6bc7bbef4`.

Earlier failed and incomplete attempts remain unchanged. The current review does not replace their outcomes.

## Accepted Energy and Operator reviews

The Energy presentation check passed using the retained training input, frozen model artifacts, isolated forecast workers, PostgreSQL, and the Monitoring API. It admitted 61 controlled observations and checked six existing validation preparations for unchanged values and envelopes. The reserved evaluation was not opened. This check covers service presentation; the existing model-accuracy and backup results retain their original scopes.

| Area | Verified result |
|---|---|
| Populated forecast | The displayed 7.662 kWh remaining-route estimate matched the service response within display rounding; the endpoint chart, forecast horizon, 350 kWh capacity assumption, and two station assessments rendered |
| Forecast freshness | Expired values disappeared from the current card while the saved forecast remained in history |
| Forecast connection | Connection loss hid the current value and showed the connection error; reconnection cleared the error |
| Vehicle selection | Switching vehicles cleared the previous vehicle's forecast history; switching back restored its own saved record |
| Operator replacement | A queued 5 m/s request was replaced by 4 m/s; the original remained superseded, the executor acknowledged the replacement, and the independent observer confirmed a peak of 4.00 m/s |
| Unresolved handoff | A claim before cancellation produced a conflict; the interface retained an unknown outcome and awaited measured evidence |
| Pending decision cancellation | A sealed pending operator decision was canceled; the timeline displayed the canceled state with no effect assessment |
| Access restrictions | Monitoring and replay credentials could not submit operator actions; another vehicle was refused; internal claim and replay command routes were unavailable |
| Captures | Three Energy images and eight Operator images, including all three command cases at desktop and 390-pixel mobile widths; zero browser page errors |
| Resources and cleanup | All four prechecks and continuous Windows/Ubuntu guards passed; minimum recorded Windows available memory was 2.625 GiB; owned resources were removed |

The Operator fixture registers one controlled signal for command verification. Full signal coverage is established by the monitoring review above. Mission intent is recorded for review; charging execution retains its separately qualified configuration.

Independent inspection found no remaining containers or review networks. The 21 preexisting volumes were preserved. All 178 Energy source fingerprints, 58 Operator frontend/tool fingerprints, and 49 service fingerprints per Operator case matched the current files. Runtime application, schema, catalog, inputs, dependency, and container files match revision `6038c11`; only qualification tools, assertions, tests, and documentation changed for this review. The earlier aggregate runner fingerprint remains associated with its original run.

| Retained report | SHA-256 |
|---|---|
| Energy presentation qualification | `4bb3f9846e7bd3425cce681212a93a03c057043fbb4f8c2ea214f10602f848bf` |
| Operator three-case qualification | `9acb0ec6452fc7841788b265694136530156ff0d25ab32fa64abcb972854cd7e` |

Final shared checks passed: 839 Python tests, 35 frontend tests, 339 original contract cases, and 81 monitoring parity cases, with strict compilation and zero privacy or secret findings. Four focused guard tests cover launch refusal, cancellation of the owned Linux invocation, final resource-sample coverage, and cleanup after a resource stop. [Reproduction](../setup/dashboard-review.md#energy-and-operator-service-checks)

## Build preparation

The first external attempt passed its host precheck with at least 6.797 GiB of available Windows memory. It stopped during frontend compilation because the container build stage lacked the signal catalog imported by the preview type check. No bus, live services, or screenshots were produced. Its source matched the submitted revision, temporary resources were absent on independent inspection, and the sleep request was released.

The monitoring and offline-check recipes now provide the catalog before compilation. The checks image also includes the dashboard review profile. The corrected runtime image build passed with matching before/after source inventories. The original attempt remains failed. [Build review](dashboard-build-review.json)

The corrected offline checks image also passed the shared engineering suite: Python tests, 35 frontend tests, 339 contract cases, 81 monitoring-contract cases, formatting, and strict type checks. Its first formatter-stopped attempt remains recorded separately. Connected browser acceptance was pending at that stage; its completed result appears above. The launcher command and workload policy are unchanged.

## Presentation correction

The retained connected review recorded 300 source messages and a passing automated browser result with 44 captures. The overall attempt remains incomplete: its final service result was absent after the operator stopped the terminal during the replay window. Owned-resource cleanup and sleep release passed. These results retain their original source identity.

The recording contains genuine stationary intervals. Vehicle speed is sampled directly from SUMO. Across 300 samples it ranges from 0 to 11.11 m/s; 114 samples report zero, including working seconds 145–180. Distance stays fixed after the stopping transition and advances after departure. The monitoring/replay review has no forecast producer, so its Energy tab reports that forecasting is not configured.

The presentation correction covers all nine tabs: rounded summaries, compact navigation, grouped measurement rows, Fleet comparisons, map-side controls, synchronized history plots, forecast summaries, activity selection, delivery-stage health, and command lifecycle. All twenty standard signals and their metadata remain accessible. Temporary monitoring and replay keys are printed in the local terminal as well as copied to the clipboard.

Offline browser checks cover all nine tabs at 1440- and 390-pixel widths, map legend containment, layer switching, measurement coverage, positive and unavailable forecast presentation, operator history, and unavailable module metrics. Captures use retained readings and explicitly labeled sample service responses. The accepted connected service results appear above.

The map bundles the reviewed OpenStreetMap road subset. Its shared JavaScript chunk exceeds Vite's 500 kB advisory size threshold (about 1.06 MB minified, 204 kB compressed). No external map requests are made. The checks and runtime container recipes include the exact public input; other local data remains excluded from their build contexts.

The presentation correction's shared checks passed with 835 Python tests, 35 frontend tests, 339 original contract cases, and 81 monitoring parity cases. Strict compilation, formatting, privacy, and secret checks passed. The source-bound runtime image also built successfully. The private review preserves source fingerprints, retained-input fingerprints, browser assertions, and desktop/mobile captures. The correction used offline inputs; the subsequent connected reviews are recorded above.

## Operator workflow and launcher review

The connected desktop and laptop review passed with one SUMO bus, 135 observations, 20 signals per observation, and six saved route-aware forecasts. Chromium checked all nine tabs, all 20 History charts, the raw export and interval summary, the bound forecast map, stale forecast suppression, and recovery after a source interruption. Source consistency, host safeguards, and cleanup passed.

The charging browser fixture exercised the global reserve alert, dismissal, a recorded decision to keep driving, the map round trip, offer expiry, temporary approval restrictions, reconnect, approval, independently reported charging, and the terminal state. It checked the dialog at desktop and narrow widths. The decisions captured in Activity belong to the current dashboard tab and run. Command and source records keep their original authority.

The committed dashboard passed an 85-working-second preview from Gare Centrale to Parc La Fontaine. Chromium signed in once, opened the recommended bay on the map, returned to the dialog, and approved Bay 2 through the Command API. The source independently reported the approach, and Activity displayed the approval and observed approach as separate events. The runner saved the preview recording and verified cleanup.

The launcher browser review exercised all ten presets at all six pacing settings, charge and service selection, map rendering, guarded start and stop interactions, pause/resume confirmation, and the saved-recording link. The launcher fixture intercepted state-changing requests. The browser and unit-check evidence supports the local SUMO release.

The approval-key startup regression staged a ready file while the launcher still reported service startup. The launcher withheld dashboard access until the approval key was ready, opened the dashboard once, and returned the same approval key on copy. The browser also verified that the key field cleared after the drive.
