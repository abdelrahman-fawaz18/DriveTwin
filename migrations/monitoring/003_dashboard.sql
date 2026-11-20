-- A transactional per-asset revision; row locking orders committed changes.
CREATE TABLE m_dashboard_heads (
 run_id uuid NOT NULL, asset_id text NOT NULL,
 revision bigint NOT NULL CHECK (revision BETWEEN 1 AND 9007199254740991),
 PRIMARY KEY(run_id,asset_id)
);
ALTER TABLE m_observations ADD COLUMN dashboard_revision bigint;
WITH numbered AS (
 SELECT run_id,event_id,row_number() OVER (
  PARTITION BY run_id,asset_id ORDER BY persisted_at,event_id
 ) AS revision FROM m_observations
)
UPDATE m_observations o SET dashboard_revision=n.revision FROM numbered n
 WHERE o.run_id=n.run_id AND o.event_id=n.event_id;
INSERT INTO m_dashboard_heads
 SELECT run_id,asset_id,max(dashboard_revision) FROM m_observations GROUP BY run_id,asset_id;
ALTER TABLE m_observations ALTER COLUMN dashboard_revision SET NOT NULL;

CREATE FUNCTION m_dashboard_next(p_run uuid,p_asset text) RETURNS bigint
 LANGUAGE sql AS $$
 INSERT INTO m_dashboard_heads VALUES(p_run,p_asset,1)
 ON CONFLICT(run_id,asset_id) DO UPDATE SET revision=m_dashboard_heads.revision+1
 RETURNING revision
$$;
REVOKE ALL ON FUNCTION m_dashboard_next(uuid,text) FROM PUBLIC;
CREATE FUNCTION m_dashboard_observation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 NEW.dashboard_revision := m_dashboard_next(NEW.run_id,NEW.asset_id);
 RETURN NEW;
END $$;
CREATE TRIGGER m_dashboard_observation BEFORE INSERT ON m_observations
 FOR EACH ROW EXECUTE FUNCTION m_dashboard_observation();

CREATE TABLE m_sample_history (
 run_id uuid NOT NULL, asset_id text NOT NULL, signal_id text NOT NULL,
 event_id uuid NOT NULL, dashboard_revision bigint NOT NULL,
 sim_time_s double precision NOT NULL, observed_at timestamptz NOT NULL,
 received_at timestamptz NOT NULL, source_id text NOT NULL,
 source_epoch bigint NOT NULL, stream_seq bigint NOT NULL, sample jsonb NOT NULL,
 PRIMARY KEY(run_id,event_id,signal_id),
 FOREIGN KEY(run_id,event_id) REFERENCES m_observations(run_id,event_id)
);
CREATE INDEX m_sample_window ON m_sample_history(run_id,asset_id,signal_id,sim_time_s,event_id);
INSERT INTO m_sample_history
 SELECT o.run_id,o.asset_id,s->>'signal_id',o.event_id,o.dashboard_revision,
 o.sim_time_s,o.observed_at,o.received_at,o.source_id,o.source_epoch,o.stream_seq,s
 FROM m_observations o CROSS JOIN LATERAL jsonb_array_elements(o.payload->'samples') s;
CREATE FUNCTION m_dashboard_samples() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO m_sample_history
 SELECT NEW.run_id,NEW.asset_id,s->>'signal_id',NEW.event_id,NEW.dashboard_revision,
 NEW.sim_time_s,NEW.observed_at,NEW.received_at,NEW.source_id,NEW.source_epoch,NEW.stream_seq,s
 FROM jsonb_array_elements(NEW.payload->'samples') s;
 RETURN NULL;
END $$;
CREATE TRIGGER m_dashboard_samples AFTER INSERT ON m_observations
 FOR EACH ROW EXECUTE FUNCTION m_dashboard_samples();

CREATE FUNCTION m_dashboard_metadata() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM m_dashboard_next(NEW.run_id,NEW.asset_id);
 RETURN NULL;
END $$;
CREATE TRIGGER m_dashboard_registration AFTER INSERT ON m_registrations
 FOR EACH ROW EXECUTE FUNCTION m_dashboard_metadata();
CREATE TRIGGER m_dashboard_clock AFTER INSERT ON m_clock_events
 FOR EACH ROW EXECUTE FUNCTION m_dashboard_metadata();
REVOKE ALL ON FUNCTION m_dashboard_observation(),m_dashboard_samples(),m_dashboard_metadata() FROM PUBLIC;
