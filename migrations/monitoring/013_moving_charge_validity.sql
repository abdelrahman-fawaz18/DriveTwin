-- Earlier development records retain their status as recorded. Only jobs
-- carrying an explicit current-context validity envelope can appear live.
ALTER TABLE m_moving_charge_jobs ADD COLUMN validity jsonb
 CHECK(validity IS NULL OR octet_length(validity::text) <= 16384);
CREATE OR REPLACE FUNCTION m_moving_charge_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Charging jobs retain their audit history'; END IF;
 IF OLD.status <> 'running' OR NEW.status='running' OR
   ROW(NEW.fence,NEW.forecast_fence,NEW.owner,NEW.assignment_sha256,NEW.request_sha256,NEW.validity,NEW.started_at)
   IS DISTINCT FROM
   ROW(OLD.fence,OLD.forecast_fence,OLD.owner,OLD.assignment_sha256,OLD.request_sha256,OLD.validity,OLD.started_at)
 THEN RAISE EXCEPTION 'Charging claim identity and completed outcomes are immutable'; END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m_moving_charge_job_guard() FROM PUBLIC;
