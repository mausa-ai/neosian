-- Draft touches are stamped by the database. Keep one cutoff for the
-- entire call, including later lock waits and batches of candidate ids.
SELECT clock_timestamp() - %(seconds)s::bigint * interval '1 second' AS cutoff
