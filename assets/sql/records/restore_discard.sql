-- Only open restore owners can release their data. Committed restore owners
-- keep the same physical record rows after they become visible.
WITH dropped AS (
    DELETE FROM {{schema}}.record_drafts
    WHERE conversation_id = $1 AND kind = 'restore' AND state = 'open' AND owner = ANY($2::bigint[])
    RETURNING owner, bytes
), removed_items AS (
    DELETE FROM {{schema}}.record_staging s USING dropped
    WHERE s.conversation_id = $1 AND s.owner = dropped.owner
), removed_records AS (
    DELETE FROM {{schema}}.turn_records r USING dropped
    WHERE r.conversation_id = $1 AND r.owner = dropped.owner
)
SELECT coalesce(sum(bytes), 0)::bigint AS freed FROM dropped
