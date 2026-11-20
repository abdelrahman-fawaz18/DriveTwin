-- Read-only replay projection boundary. No replay writes operational tables.
CREATE VIEW m_replay_packets AS SELECT run_id,asset_id,event_id,source_id,source_epoch,
 stream_seq,sim_time_s,observed_at,received_at,persisted_at,wire_sha256,raw_payload,payload,dashboard_revision
 FROM m_observations;
CREATE VIEW m_replay_seed_samples AS SELECT run_id,asset_id,event_id,signal_id,source_epoch,
 stream_seq,sim_time_s,dashboard_revision FROM m_sample_history;
CREATE VIEW m_replay_definitions AS SELECT run_id,policy_sha256,policy FROM m_run_definitions;
CREATE VIEW m_replay_recipes AS SELECT run_id,asset_id,source_id,source_epoch,manifest_sha256,raw_manifest
 FROM m_registrations;
CREATE VIEW m_replay_contexts AS SELECT run_id,asset_id,sha256,raw_context FROM m_map_contexts;
CREATE VIEW m_replay_heads AS SELECT run_id,asset_id,revision FROM m_dashboard_heads;
