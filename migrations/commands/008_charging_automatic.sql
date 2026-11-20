CREATE TABLE commanding.charging_auto_targets (
    run_id uuid NOT NULL,
    asset_id text NOT NULL,
    profile_sha256 text NOT NULL CHECK (profile_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (run_id, asset_id),
    FOREIGN KEY (run_id, asset_id) REFERENCES commanding.charging_targets(run_id, asset_id)
);

CREATE TABLE commanding.charging_auto_overrides (
    run_id uuid NOT NULL,
    asset_id text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (run_id, asset_id),
    FOREIGN KEY (run_id, asset_id) REFERENCES commanding.charging_auto_targets(run_id, asset_id)
);

CREATE TABLE commanding.charging_auto_reservations (
    command_id uuid PRIMARY KEY REFERENCES commanding.charging_claims(command_id),
    api_epoch uuid NOT NULL
);

CREATE TABLE commanding.charging_auto_grants (
    command_id uuid PRIMARY KEY REFERENCES commanding.charging_auto_reservations(command_id),
    granted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

GRANT SELECT ON commanding.charging_auto_targets TO evdt_command;
GRANT SELECT, INSERT ON commanding.charging_auto_overrides,
    commanding.charging_auto_reservations, commanding.charging_auto_grants TO evdt_command;
