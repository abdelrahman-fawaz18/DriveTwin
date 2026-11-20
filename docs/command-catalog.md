# Local simulation command catalog

A capability is a named action the bus adapter understands. For example, `set_speed_limit` can reduce the speed ceiling to 4 meters per second (m/s). A mission intent records where an operator would like the bus to go. The Phase 3 simulator acts on speed caps and leaves station requests in the journal for review.

## Actions and records

The application programming interface (API) uses HTTP, the protocol for requests to a web service. A schema defines the fields, types, and units accepted in a request. Command IDs identify the same action across the request, reservation, result, and effect records.

| Action | API and format | Preconditions and limits | How to verify |
|---|---|---|---|
| Apply a speed cap | `POST /v1/commands` in the direct profile. In arbitration, submit through `POST /v1/proposals/{proposer}` and seal through `POST /v1/authority/seal`. Both use the [speed request schema](../contracts/commands/speed-limit-1.0.0.schema.json). | Registered simulated run and bus, scoped issuer, valid reason, 2–5 m/s, and a reduction below the local ceiling. Maximum real-time validity is 30 seconds and maximum simulation window is 5 seconds. The qualified drive claims at simulated second 4. The cap stays in place for the bounded drive. | Read `GET /v1/commands/{command_id}` or the operator timeline. Compare the execution reply with the independent effect. |
| Cancel a pending cap | `POST /v1/commands/{command_id}/cancel` with `{}`. | Direct request or sealed decision, before reservation and expiry. A cancellation closes the current command chain. | Original record shows `canceled`. The simulator cannot claim it. |
| Replace a pending cap | `POST /v1/commands/{command_id}/replace` with a new [speed request](../contracts/commands/speed-limit-1.0.0.schema.json) and new ID. | Direct profile only, before reservation and expiry. The replacement passes normal admission. Both changes commit together. | Original record shows `superseded` with its successor ID. Track the successor's own execution and effect. |
| Record a station request | `POST /v1/mission-intents` with a [mission-intent request](../contracts/commands/mission-intent-1.0.0.schema.json). | Scoped operator and simulated run/bus, station on the registered catalog, and admission window up to 30 seconds. | Read `GET /v1/mission-intents/{intent_id}` or the timeline for `intent_recorded` and the catalog fingerprint. This is proof of recorded intent. Arrival and charging require their own Phase 4 capabilities and evidence. |
| Divert for charging | `POST /v1/charging/diversions` in the operator profile, or `POST /v1/charging/automatic/diversions` in the approved automatic test profile. Both use [charging request types](../src/evdt/charging_wire.py). | One registered bus at rest, current departure and station observations, eligible reviewed station, protection clear, and deadlines of one simulation second and three elapsed seconds. The automatic policy cannot select an arbitrary station. | Read `GET /v1/charging/diversions/{command_id}`. An independent observer records the actual charge, battery target, resumed motion, and mission arrival. |
| Override an automatic diversion | `POST /v1/charging/automatic/override` with the run and asset. | Operator only, isolated automatic profile. The override is irreversible for that run. If saved before the final actuation grant it prevents release. | `override_latched`; a later grant attempt is refused. The override does not reverse an already released actuation. |

An exact retry uses the original ID and bytes. A reused ID with different content conflicts. A conflict on a pending change can mean the simulator already claimed it. Refresh the timeline and read the stored request before taking further action. [Endpoint and status examples](setup/command-operations-check.md)

The command service checks the UTC deadline and an elapsed-time deadline tied to the local Linux boot. The executor also checks its simulation clock, the local ceiling, and, for arbitration, the authority number. A paused simulator cannot claim a command. Admission, handoff, and effect each establish a different fact. [Timing decision](adr/0049-command-deadlines-and-rejection-audit.md)

The executor posts an [execution result](../contracts/commands/execution-result-1.0.0.schema.json) to `/internal/v1/result`, or a refusal to `/internal/v1/refusal`. The separately authenticated observer supplies [speed evidence](../contracts/commands/speed-evidence-1.0.0.schema.json) to `/internal/v1/effect`. The qualified comparison uses two 16-sample traces from the same drive setup. For a confirmed cap, the four pre-command samples must match, the post-command maximum must be within 0.05 m/s of the cap or lower, and at least two reference readings more than 0.25 m/s above the cap must come down within that tolerance. Identical post-command readings with enough opportunity to observe the cap yield `not_observed`. Ambiguous comparisons yield `inconclusive`. Without a complete assessment, the effect field is empty. [Effect calculation](../src/evdt/command_effect.py)

## Permission matrix

Each credential is scoped to its assigned role. “Own” means the registered run and bus, plus the named proposer where applicable. In the direct profile, proposal and sealing routes are disabled for the target. Arbitration enables those routes and disables direct submission and replacement.

| Credential or component | Monitoring or replay reads | Command and intent records | Request or change action | Propose or seal | Claim, verify authority, report execution | Submit effect evidence |
|---|---|---|---|---|---|---|
| Monitoring reader | Authorized live/history data | Denied | Denied | Denied | Denied | Denied |
| Replay reader | Authorized recording through replay service | Denied | Denied | Denied | Denied | Denied |
| Operator | Requires separate monitoring credential | Own timeline, commands, intents, and sealed decision | Own direct cap, pending cancel/replace as allowed, station intent | Own operator proposal and explicit seal | Denied | Denied |
| Thermal proposer | Requires separate read credential | Denied | Denied | Own thermal proposal | Denied | Denied |
| Energy proposer | Requires separate read credential | Denied | Denied | Own energy proposal | Denied | Denied |
| Simulator executor | Requires separate read credential | Receives reserved command through claim only | Denied | Denied | Own claim, fence verification, refusal, and result | Denied |
| Effect observer | Separate scoped monitoring read for the live check | Denied | Denied | Denied | Denied | Own reserved command |

The dashboard gateway forwards only the scoped timeline and permitted operator writes. It carries the supplied operator credential, and the command service checks it again. The gateway has no operator credential of its own. The internal execution and observation routes are excluded from the gateway. [Gateway implementation](../src/evdt/monitoring/operator_routes.py) · [Command API](../src/evdt/command_api.py)

The command service uses a database login limited to the command schema and the privileges assigned by migrations. The initializer has a separate administrative login for migrations. Operator, executor, observer, and proposer credentials are service credentials and confer no database login. [Command deployment](../ops/compose/compose.commands.yaml) · [Database migrations](../migrations/commands)

The automatic charging profile adds a distinct policy credential for proposing a diversion and a distinct station credential for writing station observations. The executor can reserve the proposal and request the final one-use grant. The operator can inspect and override, but cannot submit a competing charging diversion in that profile. The observer records departure and independently reconciles effects. The automatic proposal records the approved profile, decision, model, and departure fingerprints. [Charging decision](adr/0063-qualify-bounded-automatic-charging.md) · [Speed and mission scope](adr/0056-close-local-command-scope.md)
