-- One provisional charging calculation can use an idle forecast worker slot.
CREATE TABLE m_moving_charge_jobs (
 fence bigint PRIMARY KEY CHECK(fence > 0),
 forecast_fence bigint NOT NULL UNIQUE REFERENCES m_route_energy_results(fence),
 owner uuid NOT NULL,
 assignment_sha256 text NOT NULL CHECK(assignment_sha256 ~ '^[a-f0-9]{64}$'),
 request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[a-f0-9]{64}$'),
 status text NOT NULL CHECK(status IN ('running','current','historical','failed','incomplete')),
 payload jsonb,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 CHECK((status='running') = (completed_at IS NULL)),
 CHECK(payload IS NULL OR octet_length(payload::text) <= 524288),
 CHECK((status IN ('current','historical')) = (payload IS NOT NULL)),
 CHECK(payload IS NULL OR (
  payload->>'version' IS NOT DISTINCT FROM 'moving-charge-worker-result/1.0.0'
  AND payload->>'input_sha256' IS NOT DISTINCT FROM request_sha256
 ))
);
CREATE UNIQUE INDEX m_moving_charge_one_running ON m_moving_charge_jobs((true))
 WHERE status='running';
CREATE FUNCTION m_moving_charge_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Charging jobs retain their audit history'; END IF;
 IF OLD.status <> 'running' OR NEW.status='running' OR
   ROW(NEW.fence,NEW.forecast_fence,NEW.owner,NEW.assignment_sha256,NEW.request_sha256,NEW.started_at)
   IS DISTINCT FROM
   ROW(OLD.fence,OLD.forecast_fence,OLD.owner,OLD.assignment_sha256,OLD.request_sha256,OLD.started_at)
 THEN RAISE EXCEPTION 'Charging claim identity and completed outcomes are immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER m_moving_charge_job_immutable BEFORE UPDATE OR DELETE ON m_moving_charge_jobs
 FOR EACH ROW EXECUTE FUNCTION m_moving_charge_job_guard();
REVOKE ALL ON FUNCTION m_moving_charge_job_guard() FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON m_moving_charge_jobs TO evdt_forecast;
GRANT SELECT ON m_moving_charge_jobs TO evdt_read;
