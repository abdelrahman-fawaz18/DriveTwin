CREATE TABLE commanding.moving_charge_interruptions (
 command_id uuid PRIMARY KEY,
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 evidence_sha256 text NOT NULL CHECK(evidence_sha256 ~ '^[a-f0-9]{64}$'),
 observation jsonb NOT NULL CHECK(octet_length(observation::text) <= 8192),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(command_id,run_id,asset_id),
 FOREIGN KEY(command_id) REFERENCES commanding.moving_charge_handoffs(command_id),
 FOREIGN KEY(command_id,run_id,asset_id)
  REFERENCES commanding.moving_charge_intents(command_id,run_id,asset_id)
);

CREATE TABLE commanding.moving_charge_replacements (
 command_id uuid PRIMARY KEY,
 interrupted_command_id uuid NOT NULL,
 run_id uuid NOT NULL,
 asset_id text NOT NULL,
 evidence_sha256 text NOT NULL CHECK(evidence_sha256 ~ '^[a-f0-9]{64}$'),
 payload jsonb NOT NULL CHECK(octet_length(payload::text) <= 8192),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL CHECK(expires_at > recorded_at),
 CHECK(command_id <> interrupted_command_id),
 UNIQUE(command_id,interrupted_command_id),
 FOREIGN KEY(interrupted_command_id,run_id,asset_id)
  REFERENCES commanding.moving_charge_interruptions(command_id,run_id,asset_id)
);

CREATE TABLE commanding.moving_charge_replacement_decisions (
 command_id uuid PRIMARY KEY,
 interrupted_command_id uuid NOT NULL,
 decision text NOT NULL CHECK(decision IN ('vetoed','granted')),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(command_id,interrupted_command_id)
  REFERENCES commanding.moving_charge_replacements(command_id,interrupted_command_id)
);

CREATE UNIQUE INDEX moving_charge_one_replacement_per_interruption
 ON commanding.moving_charge_replacement_decisions(interrupted_command_id)
 WHERE decision='granted';

GRANT SELECT, INSERT ON commanding.moving_charge_interruptions,
 commanding.moving_charge_replacements, commanding.moving_charge_replacement_decisions
 TO evdt_command;
GRANT SELECT ON public.m_moving_charge_held_observations TO evdt_command;
