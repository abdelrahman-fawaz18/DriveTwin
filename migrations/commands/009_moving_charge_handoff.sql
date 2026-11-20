CREATE TABLE commanding.moving_charge_intents (
 command_id uuid PRIMARY KEY,
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 job_fence bigint NOT NULL REFERENCES public.m_moving_charge_jobs(fence),
 candidate_sha256 text NOT NULL CHECK(candidate_sha256 ~ '^[a-f0-9]{64}$'),
 payload jsonb NOT NULL CHECK(octet_length(payload::text) <= 8192),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL CHECK(expires_at > recorded_at),
 UNIQUE (command_id,run_id,asset_id),
 FOREIGN KEY (run_id,asset_id) REFERENCES commanding.targets(run_id,asset_id)
);

-- A primary key shared by veto and grant makes their ordering irreversible.
CREATE TABLE commanding.moving_charge_handoffs (
 command_id uuid PRIMARY KEY,
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 decision text NOT NULL CHECK(decision IN ('vetoed','granted')),
 FOREIGN KEY (command_id,run_id,asset_id)
  REFERENCES commanding.moving_charge_intents(command_id,run_id,asset_id),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE UNIQUE INDEX moving_charge_one_grant_per_run
 ON commanding.moving_charge_handoffs(run_id,asset_id) WHERE decision='granted';

CREATE TABLE commanding.moving_charge_acknowledgments (
 command_id uuid PRIMARY KEY REFERENCES commanding.moving_charge_handoffs(command_id),
 status text NOT NULL CHECK(status IN ('applied','refused','unknown')),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

GRANT SELECT, INSERT ON commanding.moving_charge_intents,
 commanding.moving_charge_handoffs, commanding.moving_charge_acknowledgments TO evdt_command;
GRANT USAGE ON SCHEMA public TO evdt_command;
GRANT SELECT ON public.m_moving_charge_jobs,public.m_route_energy_results,
 public.m_route_energy_assignments,public.m_route_forecast_jobs,public.m_source_clocks,
 public.m_source_heads,public.m_registrations,public.m_route_contexts,
 public.m_observations TO evdt_command;
