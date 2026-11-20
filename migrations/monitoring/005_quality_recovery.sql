-- Preserve applied migration 004; include forward current-quality recovery in the audit.
CREATE OR REPLACE FUNCTION m_event_record() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rev bigint; detail jsonb; at_time timestamptz; kind text; previous_state text;
BEGIN
 IF TG_TABLE_NAME='m_quarantine' THEN
  IF NEW.asset_id IS NULL THEN RETURN NULL; END IF;
  rev:=NEW.dashboard_revision; kind:='rejection'; at_time:=NEW.received_at;
  detail:=jsonb_build_object('reason',NEW.reason);
 ELSIF TG_TABLE_NAME='m_observations' THEN
  SELECT jsonb_agg(jsonb_build_object('signal_id',s->>'signal_id','quality',s->>'quality','reason',s->>'reason')) INTO detail
   FROM jsonb_array_elements(NEW.payload->'samples') s WHERE s->>'quality' IN ('missing','invalid') OR EXISTS (
   SELECT 1 FROM m_current c WHERE c.run_id=NEW.run_id AND c.asset_id=NEW.asset_id
    AND c.signal_id=s->>'signal_id' AND coalesce(c.sample->>'quality','good')<>s->>'quality'
    AND (NEW.sim_time_s,NEW.source_epoch,NEW.stream_seq)>(c.sim_time_s,c.source_epoch,c.stream_seq)
  );
  IF detail IS NULL THEN RETURN NULL; END IF;
  rev:=NEW.dashboard_revision; kind:='quality'; at_time:=NEW.received_at;
  detail:=jsonb_build_object('event_id',NEW.event_id,'sim_time_s',NEW.sim_time_s,'signals',detail);
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
