CREATE TABLE m_sessions (
 run_id uuid NOT NULL, asset_id text NOT NULL, source_id text NOT NULL,
 source_epoch bigint NOT NULL, session_id uuid NOT NULL,
 PRIMARY KEY(run_id,asset_id,source_id,source_epoch),
 UNIQUE(run_id,asset_id,source_id,session_id)
);
CREATE TABLE m_observations (
 run_id uuid NOT NULL, event_id uuid NOT NULL, asset_id text NOT NULL, source_id text NOT NULL,
 source_epoch bigint NOT NULL, session_id uuid NOT NULL, stream_seq bigint NOT NULL,
 sim_time_s double precision NOT NULL, observed_at timestamptz NOT NULL,
 received_at timestamptz NOT NULL, persisted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 wire_sha256 text NOT NULL, raw_payload bytea NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(run_id,event_id), UNIQUE(run_id,asset_id,source_id,source_epoch,stream_seq)
);
CREATE INDEX m_history ON m_observations(run_id,asset_id,sim_time_s,event_id);
CREATE TABLE m_current (
 run_id uuid NOT NULL, asset_id text NOT NULL, signal_id text NOT NULL,
 event_id uuid NOT NULL, source_id text NOT NULL, source_epoch bigint NOT NULL,
 stream_seq bigint NOT NULL, sim_time_s double precision NOT NULL,
 observed_at timestamptz NOT NULL, sample jsonb NOT NULL,
 PRIMARY KEY(run_id,asset_id,signal_id)
);
CREATE TABLE m_quarantine (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, run_id uuid NOT NULL,
 received_at timestamptz NOT NULL, reason text NOT NULL,
 wire_sha256 text NOT NULL, raw_payload bytea NOT NULL
);
CREATE TABLE m_receipts (
 run_id uuid NOT NULL,event_id uuid NOT NULL,wire_sha256 text NOT NULL,
 receipt bytea NOT NULL,PRIMARY KEY(run_id,event_id,wire_sha256)
);
