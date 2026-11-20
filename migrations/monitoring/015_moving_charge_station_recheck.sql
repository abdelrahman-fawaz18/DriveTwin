-- Store the expiration from completion-time station revalidation separately
-- from the immutable validity recorded on the original worker claim.
ALTER TABLE m_moving_charge_jobs ADD COLUMN station_rechecked_until double precision
 CHECK(station_rechecked_until IS NULL OR (
  status='current' AND validity IS NOT NULL
  AND station_rechecked_until > 0 AND station_rechecked_until <= 1000000000000
  AND station_rechecked_until <= EXTRACT(EPOCH FROM completed_at) + 600
  AND station_rechecked_until >= EXTRACT(EPOCH FROM started_at)
 ));
