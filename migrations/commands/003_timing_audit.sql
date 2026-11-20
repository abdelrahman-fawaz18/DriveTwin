CREATE TABLE commanding.deadlines (
 command_id uuid PRIMARY KEY REFERENCES commanding.requests(command_id),
 boot_id uuid NOT NULL,
 anchored_utc timestamptz NOT NULL,
 anchor_ns bigint NOT NULL CHECK (anchor_ns >= 0),
 deadline_ns bigint NOT NULL CHECK (deadline_ns > anchor_ns AND deadline_ns - anchor_ns <= 30000000000)
);
CREATE TABLE commanding.rejections (
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 command_id uuid REFERENCES commanding.requests(command_id),
 operation text NOT NULL CHECK (operation IN ('submit','read','claim','result','effect','execute')),
 reason text NOT NULL CHECK (reason ~ '^[a-z_]{1,48}$'),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY (run_id, asset_id) REFERENCES commanding.targets(run_id, asset_id)
);
CREATE INDEX rejections_command ON commanding.rejections(command_id) WHERE command_id IS NOT NULL;
GRANT SELECT, INSERT ON commanding.deadlines, commanding.rejections TO evdt_command;
GRANT USAGE ON SEQUENCE commanding.rejections_event_id_seq TO evdt_command;
