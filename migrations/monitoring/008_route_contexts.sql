-- Source metadata is append-only and remains separate from scalar telemetry.
DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='evdt_registry') THEN
  CREATE ROLE evdt_registry NOLOGIN;
 END IF;
 IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='evdt_read') THEN
  CREATE ROLE evdt_read NOLOGIN;
 END IF;
END $$;
CREATE TABLE m_route_contexts (
 run_id uuid NOT NULL, asset_id text NOT NULL, source_id text NOT NULL,
 source_epoch bigint NOT NULL, session_id uuid NOT NULL,
 context_sha256 text NOT NULL CHECK(context_sha256 ~ '^[a-f0-9]{64}$'),
 plan_sha256 text NOT NULL CHECK(plan_sha256 ~ '^[a-f0-9]{64}$'),
 working_s double precision NOT NULL CHECK(working_s >= 0 AND working_s < 'Infinity'::float8),
 raw_context bytea NOT NULL CHECK(octet_length(raw_context) <= 65536),
 context jsonb NOT NULL, received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(run_id,context_sha256),
 UNIQUE(run_id,asset_id,source_id,source_epoch,session_id,working_s),
 FOREIGN KEY(run_id,asset_id,source_id,source_epoch)
  REFERENCES m_registrations(run_id,asset_id,source_id,source_epoch),
 CHECK(context->>'version' = 'source-route-context/2.0.0')
);
CREATE INDEX m_route_context_scope ON m_route_contexts(run_id,asset_id,source_id,source_epoch,working_s DESC);
CREATE TRIGGER m_route_contexts_immutable BEFORE UPDATE OR DELETE ON m_route_contexts
 FOR EACH ROW EXECUTE FUNCTION m_energy_immutable();
GRANT SELECT, INSERT ON m_route_contexts TO evdt_registry;
GRANT SELECT ON m_route_contexts TO evdt_read, evdt_forecast;
