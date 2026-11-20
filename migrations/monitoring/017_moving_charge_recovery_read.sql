GRANT SELECT ON m_moving_charge_held_observations TO evdt_forecast;
GRANT SELECT (run_id,asset_id,source_id,source_epoch,state,sim_time_s,observed_at,received_at)
 ON m_source_clocks TO evdt_forecast;
GRANT SELECT (run_id,asset_id,source_id,source_epoch) ON m_source_heads TO evdt_forecast;
GRANT SELECT (run_id,asset_id,source_id,source_epoch,session_id,manifest_sha256)
 ON m_registrations TO evdt_forecast;

CREATE TABLE m_moving_charge_recovery_jobs (
 fence bigint PRIMARY KEY CHECK(fence>0),
 owner uuid NOT NULL,
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 source_id text NOT NULL,
 source_epoch bigint NOT NULL,
 session_id uuid NOT NULL,
 observation_sha256 text NOT NULL CHECK(observation_sha256 ~ '^[a-f0-9]{64}$'),
 request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[a-f0-9]{64}$'),
 assignment_sha256 text NOT NULL CHECK(assignment_sha256 ~ '^[a-f0-9]{64}$'),
 status text NOT NULL CHECK(status IN ('running','current','historical','failed','incomplete')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 payload jsonb CHECK(octet_length(payload::text) <= 524288),
 UNIQUE(run_id,asset_id,source_id,source_epoch,session_id),
 FOREIGN KEY(run_id,asset_id,source_id,source_epoch,session_id)
   REFERENCES m_moving_charge_held_observations(run_id,asset_id,source_id,source_epoch,session_id),
 CHECK((status='running')=(completed_at IS NULL)),
 CHECK((status IN ('current','historical'))=(payload IS NOT NULL))
);
GRANT SELECT, INSERT ON m_moving_charge_recovery_jobs TO evdt_forecast;
GRANT UPDATE(status,completed_at,payload) ON m_moving_charge_recovery_jobs TO evdt_forecast;

CREATE FUNCTION m_charge_recovery_close_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.status <> 'running' OR NEW.status = 'running' THEN
  RAISE EXCEPTION 'Held departure evaluation already closed or invalid closure';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER m_moving_charge_recovery_close_once
 BEFORE UPDATE ON m_moving_charge_recovery_jobs
 FOR EACH ROW EXECUTE FUNCTION m_charge_recovery_close_once();
