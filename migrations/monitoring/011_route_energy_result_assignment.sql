-- Preserve the original migration and strengthen direct-writer claim binding.
CREATE OR REPLACE FUNCTION m_route_energy_result_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE job m_route_forecast_jobs%ROWTYPE;
BEGIN
 SELECT * INTO STRICT job FROM m_route_forecast_jobs WHERE fence=NEW.fence;
 IF (job.run_id,job.asset_id,job.input_sha256,job.status)
    IS DISTINCT FROM (NEW.run_id,NEW.asset_id,NEW.input_sha256,NEW.classification)
    OR job.request->'state'->'scope' IS DISTINCT FROM NEW.payload->'scope'
    OR job.generation IS DISTINCT FROM (NEW.payload->>'generation')::bigint
    OR job.request->>'assignment_sha256' IS DISTINCT FROM NEW.assignment_sha256 THEN
  RAISE EXCEPTION 'Forecast result does not match its terminal fenced claim';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION m_route_energy_result_binding() FROM PUBLIC;
