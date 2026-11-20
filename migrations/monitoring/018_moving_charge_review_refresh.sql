-- Forecast outcomes remain immutable. Only the timestamp of the latest
-- compatible station check can advance after a current calculation completes.
CREATE OR REPLACE FUNCTION m_moving_charge_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Charging jobs retain their audit history'; END IF;
 IF OLD.status='current'
    AND NEW.station_rechecked_until > OLD.station_rechecked_until
    AND (to_jsonb(NEW) - 'station_rechecked_until')
        = (to_jsonb(OLD) - 'station_rechecked_until')
 THEN RETURN NEW; END IF;
 IF OLD.status <> 'running' OR NEW.status='running' OR
   ROW(NEW.fence,NEW.forecast_fence,NEW.owner,NEW.assignment_sha256,NEW.request_sha256,NEW.validity,NEW.started_at)
   IS DISTINCT FROM
   ROW(OLD.fence,OLD.forecast_fence,OLD.owner,OLD.assignment_sha256,OLD.request_sha256,OLD.validity,OLD.started_at)
 THEN RAISE EXCEPTION 'Charging claim identity and completed outcomes are immutable'; END IF;
 RETURN NEW;
END $$;
