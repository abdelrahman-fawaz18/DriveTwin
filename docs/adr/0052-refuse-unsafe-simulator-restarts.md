# ADR 0052. Preserve command and queue state when the simulator stops

A command reservation records that the service handed the request to the simulator or may have handed it over. If the simulator stops without publishing an execution result, keep that reservation and report the outcome as unknown. The service refuses another handoff for the same request. The interrupted monitoring run retains its accepted readings and can report a disconnected source when its clock updates stop.

The monitoring adapter starts only from a fresh queue session. It checks that condition inside the same SQLite transaction that starts a session. SQLite stores the local queue on disk. The transaction ensures a rejected start leaves the existing session identifier, generation, sequence counter, and pending readings intact. A sender restart can still open and drain the existing queue.

For this local profile, resume operation by provisioning a fresh run with its own queue, source artifacts, broker state, and scoped credentials. The command and telemetry database can keep both runs. The fresh run has a new identity, so old commands and observations stay associated with the interrupted run. Resuming the exact interrupted motion state would require a saved simulator state and a separate recovery policy.

The qualification stops only the disposable source container. It covers interruption just after reservation and interruption after accepted readings show the cap. The second case deliberately drops the execution reply and keeps the partial speed trace. The full observation window is required for a formal effect assessment. [Reproduction guide](../setup/live-source-restart-check.md)
