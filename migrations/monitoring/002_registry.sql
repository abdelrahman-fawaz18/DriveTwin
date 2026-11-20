CREATE TABLE m_run_definitions (
 run_id uuid PRIMARY KEY, policy_sha256 text NOT NULL, raw_policy bytea NOT NULL,
 policy jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE m_registrations (
 run_id uuid NOT NULL, asset_id text NOT NULL, source_id text NOT NULL,
 source_epoch bigint NOT NULL, session_id uuid NOT NULL, manifest_sha256 text NOT NULL,
 minimum_sim_time_s double precision NOT NULL, raw_manifest bytea NOT NULL, manifest jsonb NOT NULL,
 registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(run_id,asset_id,source_id,source_epoch),
 UNIQUE(run_id,asset_id,source_id,session_id),
 UNIQUE(run_id,asset_id,source_id,manifest_sha256)
);
CREATE TABLE m_source_heads (
 run_id uuid NOT NULL, asset_id text NOT NULL, source_id text NOT NULL, source_epoch bigint NOT NULL,
 PRIMARY KEY(run_id,asset_id,source_id)
);
CREATE TABLE m_retired_sources (
 run_id uuid NOT NULL, asset_id text NOT NULL, source_id text NOT NULL, source_epoch bigint NOT NULL,
 maximum_sim_time_s double precision NOT NULL,
 PRIMARY KEY(run_id,asset_id,source_id,source_epoch)
);
CREATE TABLE m_clock_events (
 run_id uuid NOT NULL, asset_id text NOT NULL, source_id text NOT NULL, source_epoch bigint NOT NULL,
 event_id uuid NOT NULL, revision bigint NOT NULL, wire_sha256 text NOT NULL,
 raw_payload bytea NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(run_id,event_id), UNIQUE(run_id,asset_id,source_id,source_epoch,revision)
);
CREATE TABLE m_source_clocks (
 run_id uuid NOT NULL, asset_id text NOT NULL, source_id text NOT NULL, source_epoch bigint NOT NULL,
 revision bigint NOT NULL, state text NOT NULL, sim_time_s double precision NOT NULL,
 observed_at timestamptz NOT NULL, received_at timestamptz NOT NULL, pause_started_at timestamptz,
 PRIMARY KEY(run_id,asset_id,source_id)
);
ALTER TABLE m_observations ADD COLUMN admission jsonb NOT NULL DEFAULT '{}'::jsonb;
