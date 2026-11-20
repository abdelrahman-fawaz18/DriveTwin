CREATE TABLE m_map_contexts (
 run_id uuid NOT NULL, asset_id text NOT NULL, sha256 text NOT NULL,
 raw_context bytea NOT NULL, context jsonb NOT NULL, PRIMARY KEY(run_id,asset_id)
);
CREATE TABLE m_dashboard_events (
 run_id uuid NOT NULL, asset_id text NOT NULL, revision bigint NOT NULL,
 kind text NOT NULL, occurred_at timestamptz NOT NULL, detail jsonb NOT NULL,
 PRIMARY KEY(run_id,asset_id,revision)
);
ALTER TABLE m_quarantine ADD COLUMN asset_id text;
ALTER TABLE m_quarantine ADD COLUMN dashboard_revision bigint;
CREATE VIEW m_scoped_rejections AS SELECT run_id,asset_id,received_at,reason,dashboard_revision
 FROM m_quarantine WHERE asset_id IS NOT NULL;
CREATE FUNCTION m_event_quarantine_before() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.asset_id IS NOT NULL THEN
  NEW.dashboard_revision := m_dashboard_next(NEW.run_id,NEW.asset_id);
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER m_event_quarantine_before BEFORE INSERT ON m_quarantine
 FOR EACH ROW EXECUTE FUNCTION m_event_quarantine_before();
CREATE FUNCTION m_event_record() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rev bigint; detail jsonb; at_time timestamptz; kind text; previous_state text;
BEGIN
 IF TG_TABLE_NAME='m_quarantine' THEN
  IF NEW.asset_id IS NULL THEN RETURN NULL; END IF;
  rev:=NEW.dashboard_revision; kind:='rejection'; at_time:=NEW.received_at;
  detail:=jsonb_build_object('reason',NEW.reason);
 ELSIF TG_TABLE_NAME='m_observations' THEN
  SELECT jsonb_agg(jsonb_build_object('signal_id',s->>'signal_id','quality',s->>'quality','reason',s->>'reason')) INTO detail
   FROM jsonb_array_elements(NEW.payload->'samples') s WHERE s->>'quality' IN ('missing','invalid');
  IF detail IS NULL THEN RETURN NULL; END IF;
  rev:=NEW.dashboard_revision; kind:='quality'; at_time:=NEW.received_at;
  detail:=jsonb_build_object('event_id',NEW.event_id,'signals',detail);
 ELSE
  SELECT revision INTO rev FROM m_dashboard_heads WHERE run_id=NEW.run_id AND asset_id=NEW.asset_id;
  IF TG_TABLE_NAME='m_registrations' THEN
   kind:='registration';at_time:=NEW.registered_at;
   detail:=jsonb_build_object('source_id',NEW.source_id,'source_epoch',NEW.source_epoch,'manifest_sha256',NEW.manifest_sha256);
  ELSE
   SELECT payload->>'state' INTO previous_state FROM m_clock_events WHERE run_id=NEW.run_id AND asset_id=NEW.asset_id AND source_id=NEW.source_id AND source_epoch=NEW.source_epoch AND revision<NEW.revision ORDER BY revision DESC LIMIT 1;
   IF previous_state IS NOT DISTINCT FROM NEW.payload->>'state' THEN RETURN NULL; END IF;
   kind:='source-state';at_time:=(NEW.payload->>'observed_at')::timestamptz;
   detail:=jsonb_build_object('source_id',NEW.source_id,'source_epoch',NEW.source_epoch,'state',NEW.payload->>'state','sim_time_s',NEW.payload->'sim_time_s');
  END IF;
 END IF;
 INSERT INTO m_dashboard_events VALUES(NEW.run_id,NEW.asset_id,rev,kind,at_time,detail);
 RETURN NULL;
END $$;
CREATE TRIGGER m_event_registration AFTER INSERT ON m_registrations FOR EACH ROW EXECUTE FUNCTION m_event_record();
CREATE TRIGGER m_event_clock AFTER INSERT ON m_clock_events FOR EACH ROW EXECUTE FUNCTION m_event_record();
CREATE TRIGGER m_event_observation AFTER INSERT ON m_observations FOR EACH ROW EXECUTE FUNCTION m_event_record();
CREATE TRIGGER m_event_quarantine AFTER INSERT ON m_quarantine FOR EACH ROW EXECUTE FUNCTION m_event_record();
REVOKE ALL ON FUNCTION m_event_record(),m_event_quarantine_before() FROM PUBLIC;
