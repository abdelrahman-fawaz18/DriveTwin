CREATE TABLE commanding.charging_targets (
    run_id uuid NOT NULL,
    asset_id text NOT NULL,
    release_sha256 text NOT NULL,
    catalog_sha256 text NOT NULL,
    network_sha256 text NOT NULL,
    PRIMARY KEY (run_id, asset_id),
    FOREIGN KEY (run_id, asset_id) REFERENCES commanding.targets(run_id, asset_id)
);

CREATE VIEW commanding.charging_sources AS
SELECT t.run_id, t.asset_id, r.manifest_sha256
FROM commanding.charging_targets t
JOIN public.m_source_heads h USING (run_id, asset_id)
JOIN public.m_registrations r USING (run_id, asset_id, source_id, source_epoch)
WHERE h.source_id = 'sumo-adapter'
  AND r.manifest->'input_sha256'->>'charging-release' = t.release_sha256
  AND r.manifest->'input_sha256'->>'charging-catalog' = t.catalog_sha256
  AND r.manifest->'input_sha256'->>'charging-network' = t.network_sha256;

CREATE TABLE commanding.charging_observations (
    run_id uuid NOT NULL,
    asset_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('departure', 'stations')),
    observation_id uuid NOT NULL,
    sim_s double precision NOT NULL,
    payload jsonb NOT NULL,
    received_at timestamptz NOT NULL,
    boot_id uuid NOT NULL,
    elapsed_ns bigint NOT NULL,
    PRIMARY KEY (run_id, asset_id, kind, observation_id),
    UNIQUE (run_id, asset_id, kind, sim_s),
    FOREIGN KEY (run_id, asset_id) REFERENCES commanding.charging_targets(run_id, asset_id)
);

CREATE TABLE commanding.charging_diversions (
    run_id uuid NOT NULL,
    asset_id text NOT NULL,
    command_id uuid NOT NULL UNIQUE,
    payload jsonb NOT NULL,
    PRIMARY KEY (run_id, asset_id),
    FOREIGN KEY (run_id, asset_id) REFERENCES commanding.charging_targets(run_id, asset_id)
);

CREATE TABLE commanding.charging_claims (
    command_id uuid PRIMARY KEY REFERENCES commanding.charging_diversions(command_id),
    reserved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE commanding.charging_effects (
    command_id uuid PRIMARY KEY REFERENCES commanding.charging_claims(command_id),
    payload jsonb NOT NULL
);

GRANT SELECT ON commanding.charging_targets, commanding.charging_sources TO evdt_command;
GRANT SELECT, INSERT ON commanding.charging_observations, commanding.charging_diversions,
    commanding.charging_claims, commanding.charging_effects TO evdt_command;
