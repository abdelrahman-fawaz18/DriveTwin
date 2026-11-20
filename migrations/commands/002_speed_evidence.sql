CREATE TABLE commanding.effects (
 command_id uuid PRIMARY KEY REFERENCES commanding.attempts(command_id),
 evidence jsonb NOT NULL CHECK (octet_length(evidence::text) <= 2048),
 assessment jsonb NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (evidence->>'command_id' = command_id::text),
 CHECK (assessment->>'status' IN ('confirmed', 'not_observed', 'inconclusive'))
);
GRANT SELECT, INSERT ON commanding.effects TO evdt_command;
