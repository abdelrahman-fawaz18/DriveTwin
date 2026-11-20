GRANT SELECT ON public.m_moving_charge_recovery_jobs TO evdt_command;

CREATE TABLE commanding.moving_charge_replacement_acknowledgments (
 command_id uuid PRIMARY KEY REFERENCES commanding.moving_charge_replacement_decisions(command_id),
 status text NOT NULL CHECK(status IN ('applied','refused','unknown')),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
GRANT SELECT, INSERT ON commanding.moving_charge_replacement_acknowledgments TO evdt_command;
