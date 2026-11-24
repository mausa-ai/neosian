-- The conversation lock is held. Sample the age cutoff once after the wait;
-- a refused new part leaves each expired owner's records intact.
WITH moment AS MATERIALIZED (SELECT clock_timestamp() AS at),
previous AS MATERIALIZED (
    SELECT d.owner, d.name, extract(epoch FROM moment.at - d.touched_at) > $3::bigint AS expired
    FROM {{schema}}.record_drafts AS d CROSS JOIN moment
    WHERE d.conversation_id = $1 AND d.kind = 'restore' AND d.state = 'open'
), gate AS (
    SELECT EXISTS (SELECT 1 FROM {{schema}}.turns WHERE conversation_id = $1)
        OR EXISTS (SELECT 1 FROM {{schema}}.projections WHERE conversation_id = $1) AS occupied,
        (SELECT name FROM previous WHERE NOT expired ORDER BY name COLLATE "C" LIMIT 1) AS competing
), opened AS (
    INSERT INTO {{schema}}.record_drafts
        (conversation_id, kind, name, state, opened_at, touched_at, position, bytes)
    SELECT $1, 'restore', $2, 'open', moment.at, moment.at, $4::jsonb, $5::bigint
    FROM moment CROSS JOIN gate WHERE NOT occupied AND competing IS NULL
    RETURNING owner
)
SELECT occupied, competing, (SELECT owner FROM opened) AS owner,
       ARRAY(SELECT owner FROM previous WHERE expired) AS expired
FROM gate
