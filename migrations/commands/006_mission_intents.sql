CREATE TABLE commanding.mission_stations (
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 station_id text NOT NULL CHECK (station_id ~ '^[a-z][a-z0-9-]{0,63}$'),
 catalog_sha256 text NOT NULL CHECK (catalog_sha256 ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY (run_id, asset_id, station_id),
 FOREIGN KEY (run_id, asset_id) REFERENCES commanding.targets(run_id, asset_id)
);
CREATE TABLE commanding.mission_intents (
 intent_id uuid PRIMARY KEY,
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 station_id text NOT NULL,
 wire bytea NOT NULL CHECK (octet_length(wire) BETWEEN 1 AND 2048),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY (run_id, asset_id, station_id) REFERENCES commanding.mission_stations(run_id, asset_id, station_id)
);
GRANT SELECT ON commanding.mission_stations TO evdt_command;
GRANT SELECT, INSERT ON commanding.mission_intents TO evdt_command;
