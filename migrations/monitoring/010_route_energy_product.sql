-- New model product; original assignments/results retain their own contracts.
CREATE TABLE m_route_energy_assignments (
 run_id uuid NOT NULL REFERENCES m_run_definitions(run_id), asset_id text NOT NULL,
 source_id text NOT NULL, revision bigint NOT NULL CHECK(revision>0 AND revision<=9007199254740991),
 assignment_sha256 text NOT NULL CHECK(assignment_sha256 ~ '^[a-f0-9]{64}$'),
 assignment jsonb NOT NULL CHECK(octet_length(assignment::text)<=2097152),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(run_id,asset_id,revision), UNIQUE(run_id,asset_id,assignment_sha256),
 CHECK(assignment->>'version'='route-energy-assignment/2.0.0'),
 CHECK(assignment->>'run_id'=run_id::text AND assignment->>'asset_id'=asset_id),
 CHECK(assignment->>'source_id'=source_id AND (assignment->>'revision')::bigint=revision)
);
CREATE TRIGGER m_route_assignment_immutable BEFORE UPDATE OR DELETE ON m_route_energy_assignments
 FOR EACH ROW EXECUTE FUNCTION m_energy_immutable();

CREATE TABLE m_route_energy_results (
 fence bigint PRIMARY KEY REFERENCES m_route_forecast_jobs(fence),
 run_id uuid NOT NULL, asset_id text NOT NULL, source_id text NOT NULL,
 assignment_sha256 text NOT NULL, input_sha256 text NOT NULL CHECK(input_sha256 ~ '^[a-f0-9]{64}$'),
 result_sha256 text NOT NULL CHECK(result_sha256 ~ '^[a-f0-9]{64}$'),
 classification text NOT NULL CHECK(classification IN ('current','historical')),
 payload jsonb NOT NULL CHECK(octet_length(payload::text)<=262144),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(run_id,asset_id,assignment_sha256)
  REFERENCES m_route_energy_assignments(run_id,asset_id,assignment_sha256),
 UNIQUE(run_id,asset_id,input_sha256),
 CHECK(payload->>'version'='route-aware-result/2.0.0'),
 CHECK(payload->>'input_sha256'=input_sha256),
 CHECK(payload->'scope'->>'run_id'=run_id::text AND payload->'scope'->>'asset_id'=asset_id),
 CHECK(payload->'scope'->>'source_id'=source_id)
);
CREATE INDEX m_route_energy_history ON m_route_energy_results(run_id,asset_id,fence DESC);
CREATE FUNCTION m_route_energy_result_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE job m_route_forecast_jobs%ROWTYPE;
BEGIN
 SELECT * INTO STRICT job FROM m_route_forecast_jobs WHERE fence=NEW.fence;
 IF (job.run_id,job.asset_id,job.input_sha256,job.status)
    IS DISTINCT FROM (NEW.run_id,NEW.asset_id,NEW.input_sha256,NEW.classification)
    OR job.request->'state'->'scope' IS DISTINCT FROM NEW.payload->'scope'
    OR job.generation IS DISTINCT FROM (NEW.payload->>'generation')::bigint THEN
  RAISE EXCEPTION 'Forecast result does not match its terminal fenced claim';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m_route_energy_result_binding() FROM PUBLIC;
CREATE TRIGGER m_route_energy_result_scope BEFORE INSERT ON m_route_energy_results
 FOR EACH ROW EXECUTE FUNCTION m_route_energy_result_binding();
CREATE TRIGGER m_route_energy_result_immutable BEFORE UPDATE OR DELETE ON m_route_energy_results
 FOR EACH ROW EXECUTE FUNCTION m_energy_immutable();

CREATE TABLE m_route_energy_residuals (
 fence bigint NOT NULL REFERENCES m_route_energy_results(fence),
 observation_sha256 text NOT NULL CHECK(observation_sha256 ~ '^[a-f0-9]{64}$'),
 payload jsonb NOT NULL CHECK(octet_length(payload::text)<=8192),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(fence,observation_sha256),
 CHECK(payload->>'observation_sha256'=observation_sha256)
);
CREATE TRIGGER m_route_energy_residual_immutable BEFORE UPDATE OR DELETE ON m_route_energy_residuals
 FOR EACH ROW EXECUTE FUNCTION m_energy_immutable();
CREATE FUNCTION m_route_energy_residual_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE result m_route_energy_results%ROWTYPE;
BEGIN
 SELECT * INTO STRICT result FROM m_route_energy_results WHERE fence=NEW.fence;
 IF result.input_sha256 IS DISTINCT FROM NEW.payload->>'input_sha256'
    OR result.payload->'scope' IS DISTINCT FROM NEW.payload->'scope'
    OR result.payload->>'status' IS DISTINCT FROM 'available'
    OR (NEW.payload->>'from_working_s')::double precision IS DISTINCT FROM
       (result.payload->>'cutoff_working_s')::double precision
    OR (NEW.payload->>'predicted_net_j')::double precision IS DISTINCT FROM
       (result.payload->'summary'->'comparison_window'->>'predicted_net_j')::double precision
    OR (NEW.payload->>'until_working_s')::double precision IS DISTINCT FROM
       (result.payload->>'cutoff_working_s')::double precision +
       (result.payload->'summary'->'comparison_window'->>'duration_working_s')::double precision THEN
  RAISE EXCEPTION 'Comparison does not match its saved forecast';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m_route_energy_residual_binding() FROM PUBLIC;
CREATE TRIGGER m_route_energy_residual_scope BEFORE INSERT ON m_route_energy_residuals
 FOR EACH ROW EXECUTE FUNCTION m_route_energy_residual_binding();
GRANT SELECT ON m_route_energy_assignments,m_route_energy_results,m_route_energy_residuals TO evdt_read,evdt_forecast;
GRANT INSERT ON m_route_energy_results,m_route_energy_residuals TO evdt_forecast;
