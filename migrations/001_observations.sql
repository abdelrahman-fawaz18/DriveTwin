CREATE TABLE schema_migrations (
    version integer PRIMARY KEY,
    checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE observations (
    mode text NOT NULL,
    run_id uuid NOT NULL,
    event_id uuid NOT NULL,
    asset_id text NOT NULL,
    source_id text NOT NULL,
    session_id uuid NOT NULL,
    stream_seq bigint NOT NULL CHECK (stream_seq >= 0),
    observed_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    persisted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    catalog_version text NOT NULL,
    payload_hash text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (mode, run_id, event_id),
    UNIQUE (mode, run_id, asset_id, source_id, session_id, stream_seq)
);
CREATE INDEX observations_history ON observations (run_id, asset_id, observed_at, event_id);
CREATE TABLE quarantine (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL,
    received_at timestamptz NOT NULL,
    reason text NOT NULL,
    topic text NOT NULL,
    raw_payload bytea NOT NULL
);
CREATE TABLE ingest_counters (
    run_id uuid PRIMARY KEY,
    accepted bigint NOT NULL DEFAULT 0,
    duplicates bigint NOT NULL DEFAULT 0,
    rejected bigint NOT NULL DEFAULT 0
);
