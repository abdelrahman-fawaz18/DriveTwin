ALTER TABLE commanding.requests DROP CONSTRAINT requests_run_id_asset_id_key;
CREATE INDEX requests_target ON commanding.requests(run_id, asset_id);

CREATE TABLE commanding.pending_changes (
 command_id uuid PRIMARY KEY REFERENCES commanding.requests(command_id),
 replacement_id uuid UNIQUE REFERENCES commanding.requests(command_id),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (replacement_id IS NULL OR replacement_id <> command_id)
);
GRANT SELECT, INSERT ON commanding.pending_changes TO evdt_command;
