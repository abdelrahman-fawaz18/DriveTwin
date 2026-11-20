CREATE TABLE commanding.arbitration_targets (
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 PRIMARY KEY (run_id, asset_id),
 FOREIGN KEY (run_id, asset_id) REFERENCES commanding.targets(run_id, asset_id)
);
CREATE TABLE commanding.proposals (
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 proposer text NOT NULL CHECK (proposer IN ('operator','thermal','energy')),
 command_id uuid NOT NULL UNIQUE,
 wire bytea NOT NULL CHECK (octet_length(wire) BETWEEN 1 AND 2048),
 boot_id uuid NOT NULL,
 anchored_utc timestamptz NOT NULL,
 anchor_ns bigint NOT NULL CHECK (anchor_ns >= 0),
 deadline_ns bigint NOT NULL CHECK (deadline_ns > anchor_ns AND deadline_ns - anchor_ns <= 30000000000),
 PRIMARY KEY (run_id, asset_id, proposer),
 UNIQUE (run_id, asset_id, command_id),
 FOREIGN KEY (run_id, asset_id) REFERENCES commanding.arbitration_targets(run_id, asset_id)
);
CREATE TABLE commanding.decisions (
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 generation bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 command_id uuid NOT NULL UNIQUE REFERENCES commanding.requests(command_id),
 selected_proposer text NOT NULL CHECK (selected_proposer IN ('operator','thermal','energy')),
 assessments jsonb NOT NULL CHECK (octet_length(assessments::text) <= 2048),
 decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (run_id, asset_id),
 FOREIGN KEY (run_id, asset_id, command_id) REFERENCES commanding.proposals(run_id, asset_id, command_id)
);
GRANT SELECT ON commanding.arbitration_targets TO evdt_command;
GRANT SELECT, INSERT ON commanding.proposals, commanding.decisions TO evdt_command;
GRANT USAGE ON SEQUENCE commanding.decisions_generation_seq TO evdt_command;
