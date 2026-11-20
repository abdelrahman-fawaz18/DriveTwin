CREATE TABLE commanding.targets (
 run_id uuid NOT NULL REFERENCES public.m_run_definitions(run_id),
 asset_id text NOT NULL,
 issuer text NOT NULL,
 PRIMARY KEY (run_id, asset_id),
 UNIQUE (run_id, asset_id, issuer)
);
CREATE TABLE commanding.requests (
 command_id uuid PRIMARY KEY,
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 issuer text NOT NULL,
 wire bytea NOT NULL CHECK (octet_length(wire) BETWEEN 1 AND 2048),
 accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,
 UNIQUE (run_id, asset_id),
 FOREIGN KEY (run_id, asset_id, issuer) REFERENCES commanding.targets(run_id, asset_id, issuer)
);
CREATE TABLE commanding.attempts (
 command_id uuid PRIMARY KEY REFERENCES commanding.requests(command_id),
 sim_time_s double precision NOT NULL CHECK (sim_time_s >= 0 AND sim_time_s < 'Infinity'),
 reserved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE commanding.results (
 command_id uuid PRIMARY KEY REFERENCES commanding.attempts(command_id),
 payload jsonb NOT NULL CHECK (octet_length(payload::text) <= 2048),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (payload->>'command_id' = command_id::text),
 CHECK (payload->>'status' IN ('acknowledged', 'unknown_outcome'))
);

REVOKE ALL ON SCHEMA commanding FROM PUBLIC;
GRANT USAGE ON SCHEMA commanding TO evdt_command;
GRANT SELECT ON commanding.targets TO evdt_command;
GRANT SELECT, INSERT ON commanding.requests, commanding.attempts, commanding.results TO evdt_command;
