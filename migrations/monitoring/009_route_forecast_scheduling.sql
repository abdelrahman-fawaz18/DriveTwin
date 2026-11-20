-- The forecast role may maintain its own lifecycle; it cannot mutate telemetry.
CREATE SEQUENCE m_route_forecast_fence AS bigint MINVALUE 1 MAXVALUE 9007199254740991;
CREATE TABLE m_route_forecast_states (
 run_id uuid NOT NULL, asset_id text NOT NULL,
 state jsonb NOT NULL CHECK(octet_length(state::text) <= 16777216),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(run_id,asset_id), FOREIGN KEY(run_id) REFERENCES m_run_definitions(run_id)
);
CREATE TABLE m_route_forecast_jobs (
 fence bigint PRIMARY KEY CHECK(fence > 0), owner uuid NOT NULL,
 run_id uuid NOT NULL, asset_id text NOT NULL, generation bigint NOT NULL CHECK(generation > 0),
 input_sha256 text NOT NULL CHECK(input_sha256 ~ '^[a-f0-9]{64}$'),
 request jsonb NOT NULL CHECK(octet_length(request::text) <= 8388608),
 status text NOT NULL CHECK(status IN ('running','current','historical','failed','incomplete')),
 outcome jsonb, started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 lease_until timestamptz NOT NULL, completed_at timestamptz,
 FOREIGN KEY(run_id,asset_id) REFERENCES m_route_forecast_states(run_id,asset_id),
 UNIQUE(run_id,asset_id,generation)
);
CREATE INDEX m_route_forecast_jobs_asset ON m_route_forecast_jobs(run_id,asset_id,fence DESC);
CREATE UNIQUE INDEX m_route_forecast_one_running ON m_route_forecast_jobs((true))
 WHERE status='running';
CREATE FUNCTION m_route_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Forecast jobs retain their audit history'; END IF;
 IF OLD.status <> 'running' OR
   ROW(NEW.fence,NEW.owner,NEW.run_id,NEW.asset_id,NEW.generation,NEW.input_sha256,
       NEW.request,NEW.started_at,NEW.lease_until)
   IS DISTINCT FROM
   ROW(OLD.fence,OLD.owner,OLD.run_id,OLD.asset_id,OLD.generation,OLD.input_sha256,
       OLD.request,OLD.started_at,OLD.lease_until) THEN
  RAISE EXCEPTION 'Forecast claim identity and completed outcomes are immutable';
 END IF;
 IF NEW.status='running' OR NEW.outcome IS NULL OR NEW.completed_at IS NULL THEN
  RAISE EXCEPTION 'A claim may only close with an explicit outcome';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER m_route_job_immutable BEFORE UPDATE OR DELETE ON m_route_forecast_jobs
 FOR EACH ROW EXECUTE FUNCTION m_route_job_guard();
REVOKE ALL ON FUNCTION m_route_job_guard() FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON m_route_forecast_states,m_route_forecast_jobs TO evdt_forecast;
GRANT SELECT ON m_route_forecast_states,m_route_forecast_jobs TO evdt_read;
GRANT USAGE ON SEQUENCE m_route_forecast_fence TO evdt_forecast;
