DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='evdt_forecast') THEN
  CREATE ROLE evdt_forecast NOLOGIN;
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO evdt_forecast;
CREATE TABLE m_energy_assignments (
 run_id uuid NOT NULL REFERENCES m_run_definitions(run_id), asset_id text NOT NULL,
 source_id text NOT NULL, assignment_sha256 text NOT NULL CHECK(length(assignment_sha256)=64),
 assignment jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(run_id,asset_id)
);
CREATE TABLE m_energy_forecasts (
 sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 record_id text PRIMARY KEY CHECK(record_id ~ '^[0-9a-f]{64}$'),
 run_id uuid NOT NULL, asset_id text NOT NULL, source_id text NOT NULL,
 input_id text NOT NULL CHECK(input_id ~ '^[0-9a-f]{64}$'),
 payload jsonb NOT NULL CHECK(octet_length(payload::text)<=262144),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(run_id,asset_id) REFERENCES m_energy_assignments(run_id,asset_id),
 UNIQUE(run_id,asset_id,input_id),
 CHECK(payload->>'version'='persistent-energy-forecast/1.0.0'),
 CHECK(payload->>'record_id'=record_id AND payload->>'input_id'=input_id),
 CHECK(payload->>'asset_id'=asset_id AND payload->>'source_id'=source_id),
 CHECK(payload->>'run_id'=run_id::text),
 CHECK(payload->>'status' IN ('advisory','unavailable')),
 CHECK(payload->>'status'<>'unavailable' OR payload->'values'='null'::jsonb)
);
CREATE INDEX m_energy_latest ON m_energy_forecasts(run_id,asset_id,sequence DESC);
CREATE FUNCTION m_energy_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Energy records and assignments are append-only'; END $$;
CREATE TRIGGER energy_record_immutable BEFORE UPDATE OR DELETE ON m_energy_forecasts
 FOR EACH ROW EXECUTE FUNCTION m_energy_immutable();
CREATE TRIGGER energy_assignment_immutable BEFORE UPDATE OR DELETE ON m_energy_assignments
 FOR EACH ROW EXECUTE FUNCTION m_energy_immutable();
REVOKE ALL ON FUNCTION m_energy_immutable() FROM PUBLIC;
CREATE VIEW m_energy_observation_refs AS
 SELECT run_id,asset_id,source_id,source_epoch,session_id,event_id,stream_seq,sim_time_s,wire_sha256
 FROM m_observations;
GRANT SELECT ON m_energy_assignments,m_energy_forecasts TO evdt_forecast;
GRANT SELECT ON m_energy_observation_refs TO evdt_forecast;
GRANT INSERT ON m_energy_forecasts TO evdt_forecast;
GRANT USAGE ON SEQUENCE m_energy_forecasts_sequence_seq TO evdt_forecast;
