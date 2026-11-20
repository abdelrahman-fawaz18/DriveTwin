-- Preserve earlier jobs and allow one comparison per station state and forecast.
ALTER TABLE m_moving_charge_jobs ADD COLUMN station_state_sha256 text
 CHECK(station_state_sha256 IS NULL OR station_state_sha256 ~ '^[a-f0-9]{64}$');
ALTER TABLE m_moving_charge_jobs DROP CONSTRAINT m_moving_charge_jobs_forecast_fence_key;
CREATE UNIQUE INDEX m_moving_charge_forecast_station_state
 ON m_moving_charge_jobs(forecast_fence, station_state_sha256) NULLS NOT DISTINCT;

CREATE OR REPLACE FUNCTION m_moving_charge_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Charging jobs retain their audit history'; END IF;
 IF OLD.status <> 'running' OR NEW.status='running' OR
   ROW(NEW.fence,NEW.forecast_fence,NEW.owner,NEW.assignment_sha256,NEW.request_sha256,NEW.validity,NEW.station_state_sha256,NEW.started_at)
   IS DISTINCT FROM
   ROW(OLD.fence,OLD.forecast_fence,OLD.owner,OLD.assignment_sha256,OLD.request_sha256,OLD.validity,OLD.station_state_sha256,OLD.started_at)
 THEN RAISE EXCEPTION 'Charging claim identity and completed outcomes are immutable'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m_moving_charge_job_guard() FROM PUBLIC;
