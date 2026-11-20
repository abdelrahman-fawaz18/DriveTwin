# Operator interface readiness

P3-C1 will show the operator what was requested, what the simulator acknowledged, and what the readings confirmed. A queued request can be canceled or replaced within its profile. A reserved command can still have an unknown outcome, so the interface must preserve that state until evidence arrives. A saved mission intent is shown as a request for review.

| Backend record | Display or interaction for P3-C1 |
|---|---|
| Queued direct command | Show the cap and deadline. Offer cancel and replace while pending. Refresh after a conflict. |
| Queued sealed decision | Show the winning role and its authority number. Offer cancellation while pending. |
| Canceled or superseded | Show the change time and link to the replacement when present. |
| Reserved, result unknown | Show that handoff occurred and the outcome is unknown. Suppress another handoff. |
| Acknowledged or rejected | Show the simulator response separately from the measured effect. |
| Mission intent recorded | Show station, request time and catalog fingerprint. Keep it distinct from a dispatched route change. |

The dashboard currently uses a monitoring reader credential. P3-C1 needs a separate operator session and a same-origin route to the local command service, so the browser can send those requests to the dashboard's own address. Bind the route to the selected run and bus. Keep simulator, observer, and proposer credentials outside browser code. Allow only the operator routes listed in the run guide plus bounded reads for the timeline, admitted command parameters, and the registered station choices. Keep the replay page and monitoring-only users read-only. Reuse the existing dashboard and browser qualification fixture.

P3-C1 acceptance should cover an accepted command, a cancellation or replacement, an expired or already claimed conflict, an unknown result, confirmed effect, and a saved mission intent. Verify that the monitoring reader and replay sessions cannot submit actions. P3-C2 will review the final evidence and the Phase 4 entry criteria. [Backend run guide](command-operations-check.md)
