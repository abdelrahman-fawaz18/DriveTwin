CREATE TABLE m_moving_charge_held_observations (
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 source_id text NOT NULL,
 source_epoch bigint NOT NULL,
 session_id uuid NOT NULL,
 observation_sha256 text NOT NULL CHECK(observation_sha256 ~ '^[a-f0-9]{64}$'),
 interrupted_command_id uuid NOT NULL,
 working_s double precision NOT NULL CHECK(working_s >= 0 AND working_s < 'Infinity'::float8),
 observation jsonb NOT NULL CHECK(octet_length(observation::text) <= 8192),
 received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(run_id,asset_id,source_id,source_epoch,session_id),
 FOREIGN KEY(run_id,asset_id,source_id,source_epoch)
  REFERENCES m_registrations(run_id,asset_id,source_id,source_epoch)
);

CREATE TRIGGER m_moving_charge_held_observation_immutable
 BEFORE UPDATE OR DELETE ON m_moving_charge_held_observations
 FOR EACH ROW EXECUTE FUNCTION m_energy_immutable();
GRANT SELECT, INSERT ON m_moving_charge_held_observations TO evdt_registry;
