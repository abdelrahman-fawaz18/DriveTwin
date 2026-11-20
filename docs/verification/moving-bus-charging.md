# Moving-bus Charging Advisor verification

The moving-bus development profile uses the route-aware Energy Forecast, declared station schedules, and a separate operator command path. The earlier stationary charging release retains its own evidence. [Simulation release evidence](../release/verification.md)

| Checkpoint | Status |
|---|---|
| CA-01 · Interfaces and criteria | Typed contracts, command authority, and the initial independent criteria are recorded. A qualified planning margin remains open. |
| CA-02 · Start charge and station inputs | All ten preset defaults and ten low-charge overrides passed the live insertion checks. A new OSM route was added to a verification catalog alongside the ten presets. |
| CA-03 · Candidate planning | On-route candidates and bounded two-stop calculations passed development cases. A live 59 km preview displayed a recommendation and fallback. A real-road off-route approach and reserve calculation passed offline. |
| CA-04 · Operator and measured effects | The Montréal launcher path, Mount Royal, and an independent Montréal route completed with approved charging. A separate connected detour completed. The Orléans preview verified charging through resumption. An isolated post-handoff outage passed the protective stop check. |
| CA-05 · Replanning and automatic mode | Connected trials verified automatic arrival and the operator block in Ottawa, plus Montréal recovery with an eligible replacement and a safe stop with no replacement. |
| CA-06 · Geographic qualification | All ten presets have live default and low-charge checks. Independent routes supplied on-route, detour, and adverse-condition evidence. Burnaby passed the uncharged reference, isolated queue accounting and connected queued charging through arrival. |
| CA-07 · Operation and review | The launcher, dashboard, history, isolated replay, saved recordings, and normal shutdown are verified for the reviewed profiles. The [operating scope](#operating-scope) lists the supported modes and limits. |

The guarded database exercise verified restart fencing, immutable approval, duplicate rejection, and restored contents after the approach-energy recheck was added. The fallback browser fixture verified that an expired preferred stop requires an explicit new selection and approval for the fallback. Evidence ID `moving-charge-guarded-handoff-restore-01`.

## Connected Montréal case

The launcher browser check enabled operator confirmation on Gare Centrale–Parc La Fontaine at 11.6%, obtained the separate operator key on demand, approved the current stop, and observed measured charging and resumed driving. The 696-working-second trip saved 13,920 readings and the launcher confirmed recording and cleanup. The key was absent after shutdown. Evidence ID `live-journey-20261114T115126Z-40fd7c`.

The bus left Gare Centrale at 11.6% charge on the real Montréal road geometry. The advisor compared two simulated bays and recommended the farther one, with a 12% departure target. The operator approved the visible offer. The earlier bay’s declared occupancy made waiting longer.

| Event | Working time | Recorded result |
|---|---:|---|
| SUMO accepted the stop | 7.0 s | One new stop on the assigned road sequence |
| Charging observed | 163.2 s | Station device reported battery transfer at the selected bay |
| Departure target reached | 263.6 s | 12.0004% measured charge |
| Motion resumed | 263.8 s | Bus moved after leaving the charger |
| Destination reached | 696.0 s | Lowest recorded charge 10.720% |

The independent SUMO station export and the adapter each measured 4.192 kWh delivered at the selected bay and zero transfer at the alternative. The full connected run matched all 696 telemetry messages to their committed bytes and retained 13,920 readings. Eleven regular forecasts and one charging comparison completed without worker failures. Chromium verified the charging and resumption display, map, speed history, and normal shutdown. Replay returned 126 original observations from working seconds 150–275 plus the preceding observation that initializes the first replay frame. Its credentials provide read-only access and its package declares dispatch disabled. Backup, cleanup, and release of the temporary sleep request passed.

| Check | Evidence |
|---|---|
| Live approval, charging, resumed motion, and map | `live-journey-operator-connected-06` and `live-journey-operator-connected-full-01` |
| Source and charger energy balance | `live-journey-operator-connected-06` and `live-journey-operator-connected-full-01` |
| Complete route and braking, dwell, and destination measurements | `live-journey-operator-connected-full-01` |
| Durable grant, veto, duplicate rejection, and database restore | `worker-database-20261114T081927Z-235b17` |
| Command fencing and fresh database restore after adding the station recheck receipt | `moving-charge-station-recheck-restore-01` |
| Full journey with four served stops using an isolated fixed charging request | `charging-plant-20261114T075813Z-f90bc4` |
| Power lost after the committed stop started transferring energy | `moving-charge-plant-outage-02` |

An isolated SUMO test cut the measured bay power at working second 165.6, after delivering 0.108 kWh. The executor recorded the interruption and stopped before the next step. The last measured charge remained above 10%, the energy ledger reconciled, and the test container was removed. The test used a fixed stop request. It verifies a protective stop during a transfer. Automatic recovery to a replacement bay remains open.

### Replan from an observed charger outage

At working second 165.6, SUMO confirmed that the bus was stationary inside the original bay, its power was zero, and three scheduled passenger stops remained. Charge was 10.834%. The updated readback records the ordered road and stop fingerprints with the observed position and battery energy. Evidence ID `moving-charge-held-bay-readback-01`.

The Energy Forecast started at that measured position and charge. Staying on route without charging was forecast to fall to 9.467%. Both original bays were already behind the bus or unavailable. The test assigned a simulated replacement on the first downstream OpenStreetMap lane that fit the bay and had no passenger stop. It used a 10.25% trial planning floor. The advisor predicted a minimum of 10.598% and recommended leaving the replacement bay at 12.5%. Separate cases returned no plan when station status was unknown, the station closed before charging could begin, or the bus lacked charge for the approach. Evidence ID `moving-charge-held-replan-01`.

The isolated SUMO recovery probe read back the same position and charge before replacing the stop. It received 6.450 kWh at the new bay, kept a minimum of 10.592%, and arrived at working second 695.2 with 11.468% charge. All four passenger stops were served in order. Both station meters matched the independently accounted transfers within 0.01 J. Cleanup and source validation passed. The first run retained its plant result but failed when a finite container exited during resource sampling. The rerun used the existing finite-service resampling path and kept the same resource limits. Evidence IDs `moving-charge-held-bay-physical-recovery-01` and `moving-charge-held-bay-physical-recovery-02`.

The PostgreSQL verification used a separate producer role to register the held reading during a paused source clock. Identical registration was idempotent, a changed charge value was refused, and an uncertain first command could not authorize replacement. Veto closed its request, a running source clock blocked release, and a committed replacement grant was refused on subsequent connections after the original reply was discarded. Backup and restore preserved the database inventory. Both checks used a database fixture and started no bus. Evidence IDs `moving-charge-producer-authority-01` and `moving-charge-producer-authority-02`.

The opt-in connected profile exercised the operation through the dashboard on the same Montréal route. The first attempt stopped and cleaned up before the first approval because its eight-calculation allowance could not compare three bays at three targets. The corrected profile admitted 12 calculations, covering the approach and each target at every bay, with the same elapsed-time and memory limits. Evidence IDs `moving-charge-recovery-connected-01` and `moving-charge-recovery-connected-02`.

| Event | Working time | Connected observation |
|---|---:|---|
| Transfer began at the first bay | 163.2 s | The battery reported charge at the approved bay |
| First bay lost power | 165.6 s | SUMO paused with 10.834% charge |
| Replacement stop accepted | 165.6 s | The new forecast used the same held position and charge. The operator approved an eligible downstream bay with a 12% target. |
| Replacement began transferring | 201.8 s | The battery reported charge at the replacement bay |
| Target reached and motion resumed | 314.4 s and 314.6 s | A fresh driving forecast appeared after release |
| Destination reached | 696.0 s | Minimum measured charge 10.592%. All four passenger stops were served in order. |

The browser verified that the monitoring credential cannot use the operator API. It recorded the held state, approval, replacement transfer, departure, and map. The live run reconciled 696 telemetry messages and 13,920 readings. The isolated replay returned 166 observations from working seconds 160–325 and one initial observation, with dispatch disabled and a matching content fingerprint.

The two station meters reported 0.108 kWh at the failed bay and 4.700 kWh at the replacement, with zero transfer elsewhere. The combined energy matched the source within 0.01 J. The replacement forecast predicted a 10.598% minimum and 106.2 working seconds of active transfer. SUMO measured 10.592% and 112.6 working seconds. Backup, cleanup, and release of the temporary sleep request passed. Minimum available Windows memory during the guarded run was 2.25 GiB. Evidence review `moving-charge-recovery-connected-02/recovery-review.json`.

The predicted destination time was about 177 working seconds later than actual arrival. The onward forecast accumulated about 250 seconds across six modeled traffic signal waits. SUMO had about 124 seconds of low-speed or stationary time within 15 m of those six stop lines and passed two of them without stopping. A delay near the start of the drive changes which phase the bus encounters farther along. Charge and transfer duration passed their limits. The arrival-time error remains relevant when comparing options with similar estimated delays. Evidence review `moving-charge-recovery-connected-02/timetable-diagnosis.json`.

Two connected attempts without a downstream replacement remain incomplete. The first acknowledged the planned stop at working second 8 and saved 20 packets before the Windows memory guard ended the run. The simulated outage at working second 165.6 was still ahead. Cleanup and backup passed. The second used a local API checker without a browser or replay and stopped at preflight when the minimum fell to 1.95 GiB. Evidence IDs `moving-charge-recovery-no-option-01` and `moving-charge-recovery-no-option-02`.

The safe-stop criteria require a live API observation of an empty replacement list and a final source trace ending at the held step. The reviewer binds the replacement search to its request and held observation, checks zero speed and zero bay power, reconciles the station meter and recording, and verifies reserve and cleanup. A controlled test of the reviewer rejected a later plant step, an unbound forecast, both archived host failures, and the completed replacement run.

The completed no-replacement test passed both of those checks. At working second 165.6 the charger lost power, the bus stayed stopped, and the operator endpoint offered no replacement. The plant trace ended at the held step. Minimum charge was 10.788%. All 165 packets and 3,300 readings reconciled, and backup, cleanup and sleep release passed. The runner records the interrupted trip as `failed_or_incomplete`. The qualification reports record a passing safe-stop result. Evidence ID `moving-charge-recovery-no-option-manual-20261114-145529`.

The automatic authority exercise used PostgreSQL without starting SUMO. The same observation could not be counted twice, a changed candidate could not confirm the first one, and the same candidate at the next working second could create one approval. An operator override prevented the grant when it came first. An override after the grant reported that authority had already been issued, and the journal rejected a repeated grant. The database backup restored the scoped tables. The connected qualification profile uses on-route bays and a one-percentage-point trial margin. The dashboard exposes the operator block in that profile. The regular launcher keeps the existing modes while connected automatic execution is under qualification. Evidence ID `moving-charge-automatic-authority-01`.

The initial automatic preview was blocked when available Windows RAM reached 1.81 GiB before startup. A second preview started SUMO and failed when the policy log replaced the supervisor's binary command-log handle. The report preserves the failure and backup. A targeted follow-up confirmed that no container, network, or volume from that preview remained. Evidence IDs `moving-charge-automatic-handoff-01` and `moving-charge-automatic-handoff-02`.

The corrected Ottawa preview at 13.0% verified automatic approval after two fresh working-time readings, one stop insertion at working second 9, and acknowledgment at the reviewed downstream bay. All 100 telemetry events matched the persisted bytes, and the run saved 2,000 readings. Charge stayed above the 11% trial floor during the preview. The bus was still approaching the bay when the 100-working-second preview ended. Backup and cleanup passed. Evidence ID `moving-charge-automatic-handoff-03`.

The matched Ottawa preview used the operator API to block automatic dispatch before the bus started. The advisor still found an eligible option. SUMO recorded zero charging commands and zero transfer. The operator API displayed the saved block and refused a competing manual approval. Backup, recording, and cleanup passed. Evidence ID `moving-charge-automatic-override-01`.

The first full Ottawa automatic attempt issued approval about 6.84 real-life seconds after the first observation. SUMO inserted one stop at working second 8, received 8.017 kWh at the approved bay, reached the 14% target, and resumed at working second 509.4. The independent station meter agreed with the source to within 0.01 J. All three passenger stops were observed in order and Energy Forecast returned after departure. The lowest recorded charge was 11.673%. Windows fell below the 2 GiB reserve for more than 30 seconds, and the run stopped at the last recorded working second of 699 before reaching the destination. The partial recording retained 699 source packets. Backup, cleanup, and release of the temporary sleep request passed. Evidence review `moving-charge-automatic-full-01/automatic-partial-review.json`.

A read-only check of that backup matched one automatic confirmation, one grant and one applied acknowledgment to the command ID in the bus observations. The stored candidate matched the fingerprinted forecast request, the approved bay and the target charge. The checker also rejected a wrong bay and a duplicate grant. The final Ottawa check requires these records alongside full arrival, reserve, transfer and recording evidence.

A later Ottawa attempt ran without a browser. Minimum available Windows memory during preflight was 2.16 GiB. Automatic policy issued one grant and SUMO inserted the reviewed bay at working second 7. During the run, available memory reached 1.34 GiB and triggered the same guard. The attempt retained 16 source packets, a backup, verified resource cleanup, and release of the sleep request. Charging and arrival were outside the recorded interval. Evidence review `moving-charge-automatic-full-guarded-20261114-132742/automatic-full-review.json`.

The completed Ottawa trial automatically approved one stop 5.94 real-life seconds after the first observation. The saved journal tied the two confirming readings, one-use grant and applied acknowledgment to the same forecast and charging bay. SUMO measured 8.017 kWh of transfer, reached 14%, served all three passenger stops in order and arrived at working second 887. Minimum charge was 11.673%. All 17,740 readings reconciled, and backup, cleanup and sleep release passed. The matched override trial established that the operator can block dispatch before the grant. Evidence ID `moving-charge-automatic-full-manual-20261114-142908`.

| Connected run | Windows available before start, minimum | Windows available during run, minimum | Outcome |
|---|---:|---:|---|
| Ottawa automatic arrival, first attempt | 3.27 GiB | 1.24 GiB | Charged and resumed. Host guard ended the drive before arrival. |
| Ottawa automatic arrival, later attempt | 2.16 GiB | 1.34 GiB | Inserted the stop. Host guard ended the drive before transfer. |
| Montréal outage with replacement | 3.68 GiB | 2.25 GiB | Completed. |
| Ottawa matched operator trial | 3.91 GiB | 2.44 GiB | Completed. |
| Ottawa automatic arrival, passing run | 3.11 GiB | 2.41 GiB | Completed. |
| Burnaby queued charging, passing run | 5.12 GiB | 3.64 GiB | Completed. |

At the Windows minimum in the later Ottawa attempt, the nearby Ubuntu sample reported 6.40 GiB available inside Ubuntu and 567 MiB across the project's containers. Windows reported a 2.44 GiB working set for WSL, up from 1.48 GiB at the start of runtime sampling. Docker reported zero out-of-memory kills for the project's containers. The guard acted on available Windows RAM and its duration below reserve. A successful preflight is a snapshot of startup conditions. The runtime guard checks the load after services start.

Recomputing the saved Ottawa forecast added the predicted reserve-crossing coordinate without changing its prior calculation. A desktop browser check used the actual prepared Ottawa map with controlled advisory responses to verify the recommended bay, fallback, reserve crossing, layer switch, expiration, and observed committed bay. Evidence IDs `moving-charge-automatic-full-01/reserve-map-regression.json` and `moving-charge-map-browser-01`.

## Scope of the advisory matrix

All ten installed launcher presets accept a starting-charge override and a route-bound simulated station assignment. Each preserves its original initial charge when the override is omitted. The offline development matrix produced provisional plans for ten deliberately low-charge cases, using the retained OpenStreetMap routes and attributed raster elevations. Nine original defaults cleared reserve. The Ottawa Parliament Hill–Carleton University default at 10.5% had no feasible plan among the evaluated development bays.

The OpenStreetMap inventory counted 282 mapped charging sites in the Montréal study corridor bounds, 71 in Ottawa, and 166 in Metro Vancouver. One Montréal site was tagged `bus=designated`. These counts describe the discovery inventory. The local tests use simulated bays on actual bus lanes because site access, equipment compatibility, and current availability need separate verification.

| Preset | Original charge and decision | Development challenge | Selected departure target |
|---|---|---:|---:|
| Gare Centrale–Parc La Fontaine | 80%, continue | 11.59% | 11.70% |
| Mount Royal | 80%, continue | 12.68% | 14.21% |
| Lachine–Sainte-Anne-de-Bellevue | 80%, continue | 17.14% | 21.23% |
| Parliament Hill–Carleton University | 10.5%, no feasible plan | 11.81% | 11.94% |
| ByWard Market–Orléans | 80%, continue | 15.52% | 18.68% |
| Kanata–Ottawa center | 80%, continue | 18.37% | 18.97% |
| Waterfront Station–UBC | 80%, continue | 14.30% | 14.61% |
| Stanley Park–English Bay | 80%, continue | 13.11% | 13.33% |
| New Westminster–Burnaby Mountain | 80%, continue | 22.33% | 29.37% |
| Richmond–Steveston–UBC–Burnaby Mountain | 80%, continue | 34.01% | 35.72% |

Every challenge selected the later bay in the declared two-bay fixture and retained the earlier bay as a fallback. The percentages are rounded study inputs and targets. The launcher accepts starting charge in 0.1-point increments. The offline matrix used a 25-second search budget. Evidence IDs `charging-development-geographic-02` and `moving-charge-live-template-offline-01.json`.

### Live low-charge advice on all ten presets

| Preset | Starting charge | First recommendation input time | Departure target | Live check |
|---|---:|---:|---:|---|
| Gare Centrale–Parc La Fontaine | 11.6% | 3 working s | 12.0% | Full trip with operator-approved charging |
| Mount Royal | 12.7% | 2 working s | 14.2% | 90-working-second preview |
| Lachine–Sainte-Anne-de-Bellevue | 17.1% | 3 working s | 21.2% | 90-working-second preview |
| Parliament Hill–Carleton University | 11.8% | 2 working s | 11.9% | 90-working-second preview |
| ByWard Market–Orléans | 15.5% | 5 working s | 18.7% | 120-working-second preview with the declared road restriction |
| Kanata–Ottawa center | 18.4% | 4 working s | 19.0% | 90-working-second preview |
| Waterfront Station–UBC | 14.3% | 3 working s | 14.6% | 90-working-second preview |
| Stanley Park–English Bay | 13.1% | 3 working s | 13.3% | 90-working-second preview |
| New Westminster–Burnaby Mountain | 22.3% | 4 working s | 29.4% | 120-working-second browser and map preview |
| Richmond–Steveston–UBC–Burnaby Mountain | 34.0% | 8 working s | 35.7% | 150-working-second browser and map preview |

Each run confirmed its starting charge at SUMO insertion, saved at least one recommendation and fallback, matched all reported telemetry bytes to the database, and finished with clean backup and cleanup. The first input time is the age of the journey when the worker read the bus. Processing takes additional real-life time. Evidence ID `moving-charge-combined-live-matrix-01.json`.

### Live original-charge checks

Each preset ran for 90 working seconds at 1× with its original starting charge, monitoring, Energy Forecast, Charging Advisor, and replay. Nine runs started at 80%, forecast reserve clearance for the remaining route, and issued no charger recommendation. The Ottawa Parliament Hill–Carleton University preset kept its 10.5% start and reported no feasible plan. Each passing run saved 90 source-matched packets and 1,800 measurements, a database backup, and verified cleanup. These previews exercise startup, starting charge, advice, and shutdown. Evidence ID `moving-charge-default-and-challenge-coverage-01.json`.

The first Kanata attempt preserved its recording and backup but failed Forecast Controller shutdown. A stop signal could interrupt a job between its in-memory claim and its database commit. The controller now handles the stop at a transaction boundary. A focused regression exercises a stop during claim creation, and the corrected Kanata run finished cleanly. The failed attempt remains in the evidence record. Evidence IDs `moving-charge-default-ottawa-kanata-centre-01` and `moving-charge-default-ottawa-kanata-centre-02`.

The subsequent launcher update added a read-only navigation link on Energy Forecast and expanded the operator opt-in list to the fingerprinted Mount Royal inputs. The numerical models, station assignments, reserve, and charging search budgets used by the preset checks are unchanged. The archived advisory results were decoded and revalidated with their original saved requests. The Mount Royal browser rehearsal separately checks the new launcher and alert path.

At the Parliament Hill–Carleton preset default of 10.5%, a separate 90-working-second live run withheld a plan at both evaluations. The first evaluation projected a reserve crossing near working second 126. One bay was outside the safe approach range. Waiting at the other bay consumed too much charge. The preview saved 90 packets and two completed charging comparisons with zero failures, and verified backup and cleanup. Evidence ID `moving-charge-ottawa-default-no-plan-live-01`.

The first 59 km preview exposed a startup check that rejected an otherwise valid Energy Forecast of an infeasible uncharged trip. It also exposed expiration of the original station snapshot before charging calculations completed. The second preview verified startup with an infeasible forecast and saved its recording. Its charging comparisons still exceeded the 12-second calculation allowance. An isolated worker replay completed the full eight-calculation search in 16.1 real-life seconds with a 22-second allowance. The corrected live profile uses that allowance within the existing 30-second worker cap and revalidates the station windows against fresh observations before display. The original two attempts and their partial evidence are retained. Evidence IDs `moving-charge-advisory-long-live-01`, `moving-charge-advisory-long-live-02`, `moving-charge-isolated-long-02.json` and `moving-charge-advisory-long-live-03`.

The corrected 150-working-second preview saved three Energy Forecast results and three charging comparisons with zero worker failures. It reconciled all 150 packets and 3,000 readings and verified backup and cleanup. The first comparison took about 18.5 real-life seconds from its input snapshot to the saved result. The full journey and actuation on this preset remain separate from this preview.

A separate calculation used the 59 km Vancouver route, a declared 15% start, and an early charger that closes at working second 2,400. None of the evaluated single stops cleared the reserve. A two-stop plan called for departing the first bay at 20% and the second at 36%. Predicted minimum charge was 13.30% between the bays and 12.07% after the second bay. The geometry and elevations came from the retained real-road route. Availability and charging schedules were declared. This verifies the bounded two-stop calculation on that route. The live operator profile permits one stop per run and has its own evidence. Evidence ID `moving-charge-two-stop-geographic-01-validated.json`.

The focused two-stop test also enumerates every target pair on a small route without reusing the search's saved approach results. The bounded search selected the same earliest complete trip, returned the same plan when the offers were listed in reverse order, and returned no partial plan when its calculation budget expired.

The ordinary operator profile authorizes one charging stop per run at 1×. Recovery after an outage, automatic dispatch, and scheduled queuing have dedicated qualification profiles. [Operating scope](#operating-scope) · [Operating guide](../setup/moving-bus-charging.md)

### Connected off-route approval

The Monkland Village–Loyola case started at 10.8%. The advisor used the live charge and road position to forecast a 9.845% minimum on the assigned route. A prepared 137 m detour used real OpenStreetMap streets and NRCan elevations. The Command API bound the grant to the reviewed branch and rejoin, and the Vehicle Adapter confirmed the route and passenger-stop order before advancing SUMO.

| Result | Forecast | Observed |
|---|---:|---:|
| Lowest charge with the detour | 10.276% | 10.277% |
| Destination charge | 11.4452% | 11.4453% |
| Active charging time | 167.1 working s | 154.2 working s |
| Destination arrival | 724.3 working s | 712.0 working s |

The operator approved at the start of the drive and SUMO applied the change at working second 8. The bus received 5.983 kWh, reached its 12% target, resumed at working second 435.8, and visited all three original passenger stops in order. The source and independent charger export matched within 0.01 J. Chromium verified the reader permission boundary, approval, charge, resumption, charger location, and resumed Energy Forecast. The recording reconciled 712 packets and 14,240 readings. Backup, sleep release, and cleanup passed. Evidence ID `moving-charge-connected-detour-live-01`.

### Real-road detour calculation

A bounded search used the imported OpenStreetMap lane graph to branch from the Monkland Village–Loyola route, reach an off-route simulated bay, and rejoin the assigned route. Both paths retained the same three mapped passenger stops. The alternate lane bundle used four attributed NRCan terrain tiles. The detour increased the forecast driving distance from 2.61 km to 2.75 km.

| Starting charge | Stay on route without charging | Use the detour bay |
|---|---|---|
| 10.5% | Falls to 9.55% | Rejected because the approach falls to 9.98% |
| 10.8% | Falls to 9.85% | Forecast minimum of 10.28% and arrival at 11.45% after charging to 12% |

An isolated SUMO run used a fixed route-change instruction at working second 8 while the bus was moving at 7.08 m/s. The bus began charging at working second 281.4, took on 5.983 kWh, passed 12% charge, and resumed at working second 435.8. It reached its destination at 711.8 seconds with 11.45% charge and all three passenger stops served. The lowest measured charge was 10.277%. The independent charger export matched the battery accounting and cleanup passed. The first run stopped when the test treated a retained charging reading after resumption as a new transfer. The corrected check counted transfer only during a verified charging stop and matched the charger export. Evidence IDs `moving-charge-osm-detour-forecast-01`, `moving-charge-osm-detour-forecast-02`, `moving-charge-detour-plant-live-01`, and `moving-charge-detour-plant-live-02`.

The isolated probe established the simulator effect before the connected Command API trial. The connected alternate described above used a separately prepared development profile. At that checkpoint, operator approval in the standard launcher covered the Gare Centrale–Parc La Fontaine and Mount Royal on-route configurations.

### La Fontaine candidate diversion

A search on the installed OpenStreetMap import found two candidate lanes for a simulated bay near Phillips Square. The same road loop via Rue Cathcart, Avenue Union, Boulevard René-Lévesque Ouest, Place du Frère-André, and Place Phillips can serve either. Compared with the assigned route, the change adds about 266 m of external road lanes and retains all four passenger stops in order.

| Candidate lane | Imported lane length | Stage |
|---|---:|---|
| Avenue Union, `1041940069#0_0` | 139 m | Connected lane and passenger-stop check |
| Place Phillips, `-108603837#2_0` | 99 m | Connected lane and passenger-stop check |

### Mixed and off-route launcher layouts

The launcher has six on-route-only presets, two mixed presets, and two off-route-only presets. The revised presets use the original itinerary and a charging-specific corridor that includes the reviewed branch and rejoin. Off-route locations represent simulated equipment on imported OSM lanes with raster elevation. The six new bays passed guarded one-bus plant checks using the same route-change and stop-insertion code used by the Vehicle Adapter. Each recorded battery transfer matched the independent station meter, preserved the passenger stops in order, and reached the destination above reserve.

| Preset | Layout | Starting charge for the plant checks | Passenger stops delivered | Plant evidence |
|---|---|---:|---:|---|
| Gare Centrale to Parc La Fontaine | Mixed | 11.6% | 4 | `charging-plant-20261115T191937Z-78e5a9`, `charging-plant-20261115T193208Z-ad1f75` |
| Parliament Hill to Carleton University | Mixed | 11.8% | 5 | `charging-plant-20261115T192736Z-3763ea` |
| Stanley Park | Off-route only | 12.3% | 3 | `charging-plant-20261115T192139Z-a38c24`, `charging-plant-20261115T192843Z-6c1184` |
| New Westminster to Burnaby Mountain | Off-route only | 27.4% | 8 | `charging-plant-20261115T193550Z-bac73a` |

Recorded incomplete attempts retain the missing isolated contract mount, an Ottawa target below starting charge, a host memory admission block, a source fingerprint change during a probe, and a 2,000-working-second probe cutoff reached during the longer Burnaby trip. The passing attempts used the existing host reserve and the longer trip's bounded verification duration.

The connected La Fontaine preview produced its first forecast at working second 1 and offered Avenue Union. The reader credential was denied command access. An authenticated operator approval inserted the off-route stop at working second 7. The observed road sequence matched the reviewed diversion. The Windows memory reserve ended this connected preview before charging began. Recording backup and cleanup verified. The separate Avenue Union plant run measured transfer, resumed driving, and arrival. Evidence ID `detour-connected-lafontaine-20261115T193939Z-c4253e`.

The regression review checked all ten installed profiles and the five operator-enabled configurations. For all six detour runs it reconciled the profile fingerprints, road sequences, passenger stops, and independent charger meters. The Chromium workflow verified the correction for an expired alternate offer. The Python suite passed 693 tests and 309 subtests. The dashboard build and all 50 unit tests passed.

The Stanley Park connected preview also produced its first forecast from working second 1. The Windows memory guard stopped the full stack before the live approval completed. Backup and cleanup verified. The current-revision Stanley Park plant check applied the original reviewed diversion, delivered 11.241 kWh, served all three passenger stops, and arrived with 11.229% charge. The independently exported meter matched the recorded transfer. Evidence IDs `detour-connected-stanley-park-20261115T223257Z-ac09e0` and `charging-plant-20261115T223818Z-35cf9e`.

### Mount Royal connected rehearsal

The uncharged forecast first crossed reserve at working second 205.8 on the Mount Royal climb. With 12.7% at departure, the operator approved the later bay and SUMO inserted the stop at working second 6. The bus received 8.175 kWh, reached its 14.2% target, and resumed at working second 325.8. It passed all four mapped passenger stops in order and arrived at working second 724 with 11.611% charge. The lowest charge was 11.182% against a forecast of 11.185%. Transfer took 196 working seconds against a forecast of 185.6 seconds. The charger export and vehicle ledger reconciled, the live forecast resumed, and the session backed up all 14,480 readings and verified cleanup. Evidence ID `moving-charge-operator-hill-live-01`.

A second complete run used the standard launcher. The desktop browser verified the explicit opt-in, the Energy Forecast link to the Charging Advisor, access to the separate operator key, accepted stop insertion, measured transfer, and resumed motion. The bus again received 8.175 kWh, served all four stops in order, and reached the destination at working second 724. The launcher showed completion and recording saved. The operator key became unavailable. All 14,480 readings reconciled, and backup, service cleanup, and the sleep-prevention release passed. Available Windows memory stayed at or above 2.27 GiB. Evidence ID `live-journey-20261114T141343Z-5cda63`.

An offline comparison used the retained Mount Royal roads and elevations, charger schedules, and forecast input. It changed only the assumed battery charge at the saved cutoff. The 13.6% case triggered a charger comparison even though predicted charge recovered above 10% by arrival. The forecast located the first crossing at working second 272.56 and the advisor recommended the later bay at a 14.2% departure target. At 14.0%, minimum predicted charge cleared reserve and the advisor skipped station calculations.

| Charge at the saved cutoff | Lowest charge without charging | Arrival charge without charging | Advisor result |
|---|---:|---:|---|
| 13.6% | 9.780% | 10.209% | Proposed charging stop |
| 14.0% | 10.180% | 10.609% | Continue on the assigned route |

The worker results were checked against their inputs and station constraints. The retained source observations and original evaluation record were preserved. Evidence ID `moving-charge-mount-royal-recovery-replay-01.json`.

### Orléans connected rehearsal

The 1,100-working-second suburban preview used the ByWard Market–Place d’Orléans route, a 15.5% starting charge, and the route’s declared speed restriction. Chromium verified that the reader could not approve a stop and the operator could. The adapter applied one stop, reached the intended bay, received 16.908 kWh, and reached the 18.7% target. The bus resumed at working second 987.4. Transfer took 405.6 working seconds against a forecast of 384.8. The charged preview stayed at or above 13.845%, matched the charger export and all 22,000 readings, restored live forecasting, and passed backup and cleanup. The recording covers the drive through resumption, with two passenger stops independently observed before the preview boundary. Destination and the remaining stops require a complete trip. Evidence ID `moving-charge-operator-orleans-live-01`.

### Independent route and braking diagnosis

The frozen Ottawa Lansdowne–Hunt Club reference drove 6.01 km on OpenStreetMap roads with seven mapped passenger stops and attributed elevations. Six of seven forecasts returned results. The original result remains failed because the forecast at working second 484 rejected the approach to a passenger stop. Its braking check used the 3D geometry projected from lane position. SUMO had about 28.79 m left in its own lane coordinates, versus about 28.67 m on the prepared shape.

The opt-in source-distance model accounts for that difference during the initial braking projection. It retains the physical road distance for grade and energy calculations. Reprocessing the same Ottawa observations returned seven forecasts, including the failed cutoff. All 70 retained geographic requests reproduced their original results under the original policy in Ubuntu. All 70 also completed under the new policy in a separate offline comparison. Reprocessing a known outcome is regression evidence. Fresh independent charging and runtime qualification remain separate.

A separate reference collected after the source-distance correction used the Monkland Village–Loyola Campus route. It covered 2.61 km and three mapped passenger stops on real Montréal roads. Terrain preparation attached four attributed raster tiles. At the declared 11.6% departure charge, all seven cutoffs passed the existing Energy Forecast criteria against SUMO's recorded battery trajectory. Maximum absolute arrival error was 0.00184 percentage points, and worker p95 was 1.45 real-life seconds. The candidate remains explicit in development profiles. The earlier Angrignon–Verdun preparation remains rejected for conflicting terrain and surface heights at a junction. Evidence IDs `moving-charge-independent-reference-03` and `moving-charge-independent-inputs-02`.

The connected low-charge case on that route began at 10.5%. The operator approved the preferred of two simulated bays. SUMO inserted one stop at working second 8, began transfer at 257.2, reached 11% at 334.0, resumed at 334.2, and reached the destination at 612.0. The run reconciled 612 observations and 12,240 readings. Chromium verified the authority boundary, recorded transfer, resumption, and map. The independent charger export matched the source, backup and cleanup passed, and minimum available Windows memory during the guarded session was 2.23 GiB. Evidence ID `moving-charge-independent-operator-03`.

| Quantity | Forecast | Observed |
|---|---:|---:|
| Arrival battery charge | 10.4515% | 10.4512% |
| Active transfer duration | 74.0 working s | 76.8 working s |
| Lowest observed charge | | 10.066% |
| Energy delivered | | 3.208 kWh |

### Matched comparison against the earlier feasible bay

The first matched earlier-bay attempt stopped when the browser did not select the alternate within its test deadline. It transferred no energy and passed backup and cleanup. The selector now has a stable accessible name. If an explicit choice expires, the dashboard requires a new selection before approval. The browser fixture verified both the alternate selection and the expired-offer behavior. Evidence ID `moving-charge-matched-earliest-01`.

The corrected matched run used the same route, 10.5% starting charge, charger schedules and forecast profile. The operator explicitly chose the earlier feasible bay. Both runs reached the destination above reserve, with one charging stop each.

| Decision | Destination | Energy delivered | Lowest observed charge |
|---|---:|---:|---:|
| Advisor's preferred later bay | 612 working s | 3.208 kWh | 10.066% |
| Earlier feasible bay | 609 working s | 2.133 kWh | 10.129% |

The forecast separated the options by only 0.13 working seconds in favor of the later bay. The earlier bay arrived three working seconds sooner in SUMO and had more reserve headroom at its lowest point. This near-tie provides a test case for ranking by reserve when predicted delays are within model error. It provides no measured delay benefit for the advisor on this route. Evidence ID `moving-charge-matched-earliest-02`.

The timeline review matched the two profiles, verified each saved forecast against its original request, and reconciled each station meter to 0.01 J. The earlier bay transferred for 68.6 working seconds against a forecast of 76.6. The later bay transferred for 76.8 against a forecast of 74.0. Those measurements explain most of the observed separation between two nearly equal forecast arrival times. [Trip timeline](../engineering/moving-bus-charging.md#when-the-two-options-are-close) · Evidence ID `moving-charge-matched-timeline-review-01.json`.

A separate review compared the first saved pair of options on all ten preset challenges. Under the declared queues, the model estimated an arrival advantage for the preferred later bay in all ten cases. The estimated advantage ranged from 90 to 1,170 working seconds, with a median of 307 seconds. Four of the ten earlier options had more forecast reserve headroom. The figures compare estimates from the same input snapshot. The matched runs above are the measured policy comparison. Evidence ID `moving-charge-planned-wait-comparison-01.json`.

Replaying those ten comparisons with reserve as the deciding factor within 1, 3, 10, and 20 working seconds of the fastest estimate changed none of the ten choices. The cases have larger predicted arrival differences. The Montréal matched result shows why independent close-call comparisons are needed before choosing a near-tie rule. Evidence ID `moving-charge-ranking-window-sensitivity-01.json`.

### Planning margin sensitivity

The hard reserve is 10%. An additional one-percentage-point margin would require the prediction to stay at or above 11%, leaving room for some forecast error. An offline sensitivity exercise used the existing maximum charge-error criterion to set that trial margin and replayed three development inputs with their declared equipment demand and with 5 kW more demand. The latter is a stress input for heaters, fans, and other equipment.

| Development case | Lowest predicted charge with 5 kW extra demand and the existing 10% floor | Result with an 11% planning floor |
|---|---:|---|
| Gare Centrale–Parc La Fontaine | 10.22% | Neither evaluated bay clears the floor on approach |
| ByWard Market–Orléans | 11.68% | A plan remains available |
| New Westminster–Burnaby Mountain | 15.73% | A plan remains available |

The trial margin also rejected the Montréal case at its original equipment demand. The exercise quantifies the availability cost of a stricter threshold in these scenarios. The installed configuration retains its development margin pending broader independent checks and a review of how to handle the loss of options. Evidence ID `moving-charge-margin-sensitivity-01.json`.

At authorization, the Command API and Vehicle Adapter check that current charge still covers the full predicted approach and wait plus reserve. The check conservatively grants no credit for distance covered since the forecast cutoff. Five retained operator cases cleared the new threshold at the unchanged 10% reserve. This calculation checks current input consistency independently of the trial planning margin. Evidence ID `moving-charge-handoff-headroom-replay-01.json`.

A connected Montréal preview then verified that stricter handoff and the dashboard’s approach state. The browser saw **Approaching approved charger** before any transfer appeared. The measured transfer was 4.192 kWh and the bus resumed at working second 263.8. All 6,500 readings reconciled, a new Energy Forecast appeared after resumption, and backup, source and cleanup checks passed. Evidence ID `moving-charge-guarded-approach-live-01`.

#### Independent adverse-condition reference

The Tunney’s Pasture–Westboro Village reference uses 4.33 km of OpenStreetMap roads, three mapped passenger stops, and four attributed terrain tiles. Its scenario combines a declared 23,000 kg bus, 15 kW equipment load, a 1.18 rolling-resistance multiplier, 24-second passenger dwells, and a speed factor of 0.9. The route, scenario, scoring rules, and 11% trial planning floor were recorded before collecting the first measured reference.

Starting at 13%, the uncharged bus finished the 707-working-second trip with a minimum charge of 9.800%. All seven forecasts flagged that it would fall below the 10% reserve. Their predicted minima ranged from 9.751% to 9.779%. The largest minimum-charge error was 0.050 percentage points. Cleanup passed. The first invocation was blocked before SUMO started because Windows denied Ubuntu access in the sandbox. Evidence IDs `moving-charge-margin-reference-01` and `moving-charge-margin-reference-02`.

The connected operator run used the same route and starting charge with two simulated bays and the 11% trial planning floor. SUMO accepted the later bay at working second 8. The bus received 8.017 kWh, reached its 14% target, resumed at working second 509.4, and reached the destination at working second 887. All three passenger stops were observed in order. The 887 source packets matched the stored bytes and produced 17,740 readings. The first connected attempt expired before the handoff after a delayed browser attachment. It stopped with no applied command and saved its partial recording. Evidence IDs `moving-charge-margin-operator-02` and `moving-charge-margin-operator-03`.

| Quantity | Forecast | Measured |
|---|---:|---:|
| Lowest charge with the stop | 11.682% | 11.673% |
| Destination charge | 12.220% | 12.249% |
| Active transfer | 189.5 working s | 192.2 working s |
| Destination arrival | 886.9 working s | 887.0 working s |

The independent SUMO charger export matched the recorded battery transfer within 0.01 J. A newer Energy Forecast appeared after departure from the bay. Backup, cleanup, and release of temporary sleep prevention passed. Available Windows memory stayed at or above 2.44 GiB.

#### Independent Burnaby reference

An offline import into a temporary catalog verified the Burnaby station assignment against both prepared networks and checked the 12.3% starting-charge override. The imported preset offered advice. All ten existing assignments also passed the lane and itinerary checks with their original defaults and operator permissions.

The Edmonds Station–Chiselhampton Street reference traveled 4.50 km on OpenStreetMap roads, served four mapped passenger stops, and used five attributed elevation tiles. The declared profile uses 24,000 kg, 16 kW of equipment demand, a 1.15 rolling-resistance multiplier, and 22-second passenger dwells. The route, numerical policy, and acceptance criteria were fixed before collecting the reference. An initial route continuing across the Kensington Avenue bridge was rejected because adjacent road-surface heights conflicted. That preparation collected no bus readings.

At a 14.3% start, the uncharged reference stayed above the trial floor, reached 12.066% at the destination, and recorded a minimum of 11.576%. All seven forecast cutoffs passed. The largest overprediction of minimum charge was 0.031 percentage points. No charging was needed in that reference. A separate connected case starts at 12.3%, with a predicted uncharged minimum of 9.606% and the existing 11% trial planning floor. Its first attempt recommended the early bay with a declared wait and refused approval while the bay was occupied. The later bay did not clear the approach reserve. Windows then fell below 2 GiB for more than 30 seconds. The run saved 19 source packets, recorded zero route changes and zero transfer, and passed backup, cleanup, and release of temporary sleep prevention. This case exposed the difference between an advisory plan that accounts for a queue and the current actuator, which requires a bay to be available at handoff. Evidence IDs `moving-charge-burnaby-inputs-01`, `moving-charge-burnaby-inputs-02`, `moving-charge-burnaby-reference-02`, and `moving-charge-burnaby-operator-full-01`.

Two isolated Burnaby probes kept the bay at zero power until its scheduled opening at 150 working seconds and the end of setup at 160 working seconds. The bus reached 14% charge, resumed driving, and completed all four passenger stops. The charger meter recorded 7.049 kWh. The first probe compared the wait against a sample taken after transfer began. The corrected measurement showed the same battery energy at queue arrival and immediately before transfer. Both probes failed the queue energy check. Evidence IDs `moving-charge-burnaby-queue-plant-01` and `moving-charge-burnaby-queue-plant-02`.

The scheduled-queue profile binds approval to the reviewed opening time and accounts for equipment draw in the same battery ledger as driving and charging. The executor checks the observed stop position before enabling power and holds the bus if another waiting step would take it below the 10% reserve. The corrected isolated probe measured 0.383 kWh of battery draw while waiting and 7.434 kWh of transfer after setup. It finished with all four stops served. Evidence ID `charging-plant-20261114T202302Z-d748e9`.

The first connected queue attempt approved during the opening passenger dwell. The three-second handoff lease expired before the bus could move, and the checker stopped the run at working second 16. Two subsequent preflight blocks stayed below the 2 GiB Windows reserve throughout the sample and never started the bus. The corrected observer waits for a fresh reading after passenger departure and uses the existing approval and grant checks.

| Connected Burnaby observation | Result |
|---|---:|
| Moving reading used for approval | 30 working s |
| SUMO inserted the stop | 32 working s |
| Bus entered the queue | 73.6 working s |
| Charging started after setup | 160 working s |
| Battery energy used while waiting | 0.383 kWh |
| Energy delivered to the battery | 7.434 kWh |
| Driving resumed and the next driving forecast used a new reading | 399.8 and 400 working s |
| Minimum charge | 11.664% |
| Destination and all four passenger stops | 920 working s |

The live checker matched the mapped reserve crossing to the saved decision, observed zero transfer during the wait, and saw the driving forecast withhold its result during measured transfer. All 18,400 readings reconciled. Backup, service cleanup, and sleep release passed. Evidence IDs `moving-charge-burnaby-queue-live-20261114-153636`, `moving-charge-burnaby-queue-live-20261114-155820`, `moving-charge-burnaby-queue-live-20261114-160956`, and `moving-charge-burnaby-queue-live-20261114-164603`.

A desktop browser check with recorded Ottawa geometry and controlled service responses verified that ordinary operator mode withholds approval for an occupied bay. The dedicated profile submitted one request for the reviewed offer. An observed zero-transfer waiting state displayed the wait in **Operator** and the committed bay in **Map**. The browser check used no live bus. Evidence ID `moving-charge-queue-browser-02`.

A second desktop check verified the recommendation's ranking explanation, review window in working time and UTC, and the reason a bay failed the wait check. A no-plan result cleared the bay explanations when it expired, even without a reserve-crossing location. Evidence ID `moving-charge-decision-browser-01`.

The combined Operator, Energy Forecast and Map desktop check passed 12 assertions using controlled responses and saved geographic evidence. Energy Forecast displayed 3.508 kWh measured at working second 401 of the completed Ottawa operator trip. It withdrew the active transfer message after a failed charging data request, then displayed the saved post-charge forecast from working second 510 when it was presented as the current result. Evidence ID `moving-charge-energy-browser-03`.

### Accuracy across five completed connected cases

This comparison uses the saved forecast for the bay where transfer was observed. Each forecast result matched the fingerprint of its saved request. Charge errors are in percentage points. Positive values mean the forecast was higher or later than the observation. The charger-arrival comparison uses charge at the first observed transfer.

| Case | Charge at charger | Lowest charge | Destination charge | Active transfer time | Destination time |
|---|---:|---:|---:|---:|---:|
| Mount Royal | −0.016 | +0.003 | +0.002 | −10.4 s | −9.9 s |
| Tunney’s Pasture–Westboro | −0.005 | +0.009 | −0.029 | −2.7 s | −0.1 s |
| Independent Montréal on-route | −0.010 | <0.001 | <0.001 | −2.8 s | −3.6 s |
| Monkland–Loyola detour | −0.011 | −0.001 | <0.001 | +12.9 s | +12.3 s |
| Montréal replacement after outage | −0.003 | +0.006 | −0.263 | −6.4 s | +176.8 s |
| **Median absolute error** | **0.010** | **0.003** | **0.002** | **6.4 s** | **9.9 s** |
| **Largest absolute error** | **0.016** | **0.009** | **0.263** | **12.9 s** | **176.8 s** |

All five cases arrived above reserve, served their assigned passenger stops, and met the frozen charge and transfer-duration limits. Times are working seconds. The largest destination-time error occurred in the replacement forecast, which took the bus’s held position as its starting point and overestimated downstream signal waits. This matters when two options have similar predicted arrival times. The figures describe these five completed operator cases.

The local aggregate `moving-charge-completed-accuracy-01.json` records the source and request fingerprints for all five cases. The retained Montréal on-route comparison uses `operator-evidence-review-02.json`.

### Final verification

The final database integration check verified migration idempotency, worker and role restrictions, charging handoff, interrupted-claim fencing, and a matching inventory after restoration into a fresh database. It used the same runtime source as the completed Ottawa and Burnaby cases. Cleanup and host checks passed. Evidence ID `worker-database-20261114T222744Z-0bef62`.

The final shared suite passed 1,206 Python tests and 306 subtests, 46 dashboard tests, 339 portable contract cases and 81 monitoring contract checks. The candidate privacy guard reviewed 1,142 files. Both privacy scans reported zero findings. Local report ID `f4c373743ab744e282b8582909d74a3b`.

The connected launcher, history and replay reports retain their tested runtime revisions. The final review matched the Mount Royal launcher run to `6a2cb6c`, the Montréal history and shutdown browser run to `f779ac2`, and the replacement and isolated replay case to `d9edbec`. The later browser fixture verified the changed Operator, Map and Energy Forecast display states. The current Ottawa, Montréal safe-stop and Burnaby reports match the final runtime source.

The shared check after connected recovery passed 1,198 Python tests and 303 subtests, 45 dashboard tests, 339 portable contract cases, and 81 monitoring contract checks. The candidate privacy guard reviewed 1,134 files and the secret scanner returned zero findings. Local report ID `02a5b5f644f044118c20bd3fc38eb2f9`.

The shared check after the map and queue implementation passed 1,205 Python tests and 306 subtests, 46 dashboard tests, 339 portable contract cases, and 81 monitoring contract checks. The privacy guard reviewed 1,141 files and the secret scanner returned zero findings. Local report ID `f7019dfa1d924877991197d60642e6bc`.

The shared check after adding the charging state to Energy Forecast passed the same suite, including type, contract, and privacy checks. Local report ID `47fabe768720480b86cd0b0263e8693d`.

### Additional routes with the standard launcher policy

Three routes beyond the ten presets passed a fresh standard Energy Forecast qualification on OpenStreetMap roads with attributed elevations. SUMO supplied the reference battery readings. All 21 forecasts were available, the largest absolute arrival-charge error was 0.217 percentage points, and zero forecasts passed reserve when the reference fell below it. The model and vehicle policy were reused without route-specific training. Evidence ID `map-journey-20261114T230800Z-db94bf`.

| Route | Distance | Forecast cutoffs | Advice with an 80% start | Advice with a low start |
|---|---:|---:|---|---|
| Montréal, Monkland Village–Loyola Campus | 2.61 km | 7 | Continue | Provisional stop at 10.7% starting charge |
| Ottawa, Tunney’s Pasture–Westboro Village | 4.33 km | 7 | Continue | Provisional stop at 12.3% starting charge |
| Burnaby, Edmonds Station–Chiselhampton Street | 4.50 km | 7 | Continue | Provisional stop at 11.9% starting charge |

The advice check appended the routes to a temporary launcher catalog, prepared two simulated bays per route, verified the starting charge in each generated SUMO vehicle file, and evaluated the planner from the recorded departure readings. Launcher import requires the standard qualification so separate development-policy passes remain scoped to their tested profiles.

The standard Ottawa profile also completed a 95-working-second connected preview at 12.3%. The Monitoring API returned a provisional charging stop while the bus was moving at working second 5. The session saved three forecasts and all 95 observation events. The starting-charge check, backup, and cleanup passed. Evidence ID `live-journey-ca07-new-route-audit-01`.

### Dashboard sign-in and charging review

The browser checks covered one sign-in, the top-bar shortcut, keeping the current route, temporarily unavailable approval at a junction or a stop, and the previous recommendation after expiry. The same flow fit the desktop and narrow layouts. In the live 70-working-second Lafontaine preview at 11.6%, one key opened monitoring and authorized approval. The reader key remained scoped to reading. Approval succeeded 30.23 real-life seconds after the first offer was read from the Monitoring API, and SUMO reported the approach to bay 2 at working second 36.2. The preview saved its recording and verified cleanup. Evidence ID `live-journey-charging-ux-05`.

The database qualification accepted a later station-check timestamp and rejected a backward timestamp or a change to the stored calculation. The command journal and database restore checks passed. Evidence ID `worker-database-20261115T020210Z-5d613f`.

### Operating scope

| Mode | Verified scope and availability |
|---|---|
| Advice at 1× | All ten original launcher configurations passed bounded live startup and advice checks at their original and low starting charges. The four revised variants have separate per-bay checks described above. Newly prepared routes can use the same model after the route and station assignment pass import validation. |
| Launcher operator approval | Mount Royal supports one on-route stop. The reviewed La Fontaine, Parliament Hill–Carleton, Stanley Park, and Burnaby Mountain variants also support operator approval and include off-route bays. The operator can select an eligible alternate and withdraw approval before execution takes authority. |
| Detour and charger outage | Separate Montréal profiles verified a reviewed detour through arrival, an operator-approved replacement after power loss, and a stopped bus when no replacement was feasible. Broader recovery coverage needs evidence from other routes and station conditions. |
| Automatic dispatch | The guarded Ottawa profile verified one automatic stop through arrival and a matched operator block. The regular launcher offers advice and explicit operator approval. General automatic operation needs wider reserve-margin and station-condition qualification. |
| Scheduled queue | The guarded Burnaby profile verified waiting, metered transfer, resumption and arrival. Ordinary launcher operator approval applies to currently available bays. |
| Multiple stops | Bounded two-stop planning passed independent enumeration on a small case and a real-road development calculation. Live authorization covers one planned charging stop, plus a separately reviewed outage replacement. |
| Maps and Energy Forecast | Connected service responses matched the approved plan and measured effects. Desktop verification used the dashboard with live operator cases and recorded inputs for display states. |

The 10% reserve applies throughout the trip. Ottawa and Burnaby exercised an 11% trial planning floor under different vehicle and station conditions. The evidence establishes the floor for those profiles. Generalizing a margin requires more independent routes and operating conditions, especially near reserve and where an additional margin removes every option. Station availability, connector capability and charger power are declared simulation inputs. The station catalog does not establish real-time access to public chargers.
