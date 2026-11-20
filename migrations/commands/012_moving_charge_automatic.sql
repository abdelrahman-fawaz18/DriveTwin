CREATE TABLE commanding.moving_charge_auto_previews (
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 job_fence bigint NOT NULL REFERENCES public.m_moving_charge_jobs(fence),
 candidate_sha256 text NOT NULL CHECK(candidate_sha256 ~ '^[a-f0-9]{64}$'),
 first_working_s double precision NOT NULL CHECK(first_working_s BETWEEN 0 AND 14400),
 first_observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (run_id,asset_id,job_fence),
 FOREIGN KEY (run_id,asset_id) REFERENCES commanding.targets(run_id,asset_id)
);

CREATE TABLE commanding.moving_charge_auto_approvals (
 command_id uuid PRIMARY KEY,
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 job_fence bigint NOT NULL,
 confirmed_working_s double precision NOT NULL CHECK(confirmed_working_s BETWEEN 0 AND 14400),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY (run_id,asset_id,job_fence)
  REFERENCES commanding.moving_charge_auto_previews(run_id,asset_id,job_fence),
 FOREIGN KEY (command_id,run_id,asset_id)
  REFERENCES commanding.moving_charge_intents(command_id,run_id,asset_id)
);

CREATE TABLE commanding.moving_charge_auto_overrides (
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (run_id,asset_id),
 FOREIGN KEY (run_id,asset_id) REFERENCES commanding.targets(run_id,asset_id)
);

GRANT SELECT, INSERT ON commanding.moving_charge_auto_previews,
 commanding.moving_charge_auto_approvals, commanding.moving_charge_auto_overrides TO evdt_command;
