-- Sample the age cutoff once, after this conversation's lock was acquired.
WITH moment AS MATERIALIZED (SELECT clock_timestamp() AS at), stale AS (
    DELETE FROM {{schema}}.record_drafts USING moment
    WHERE conversation_id = $1 AND kind = 'write' AND state = 'open'
      AND touched_at < moment.at - $3::bigint * INTERVAL '1 second'
    RETURNING owner, bytes
), removed AS (
    DELETE FROM {{schema}}.turn_records AS r USING stale
    WHERE r.conversation_id = $1 AND r.owner = stale.owner
), opened AS (
    INSERT INTO {{schema}}.record_drafts
        (conversation_id, kind, name, state, actor, turn_bytes,
         opened_at, touched_at, position, bytes)
    SELECT $1, 'write', $2, 'open', $4::text, $5::bigint, moment.at, moment.at,
           $6::jsonb, $7::bigint FROM moment
    WHERE (SELECT count(*) FROM {{schema}}.record_drafts
           WHERE conversation_id = $1 AND kind = 'write' AND state = 'open'
             AND touched_at >= moment.at - $3::bigint * INTERVAL '1 second') < 16
    RETURNING owner
)
SELECT (SELECT owner FROM opened) AS owner,
       (SELECT coalesce(sum(bytes), 0)::bigint FROM stale) AS freed
