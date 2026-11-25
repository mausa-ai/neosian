-- Run after acquiring the conversation lock. Only the owners removed by
-- this statement can authorize a dependent deletion or earn a refund.
WITH dropped AS (
    DELETE FROM {{schema}}.record_drafts
    WHERE conversation_id = $1 AND state = 'open' AND touched_at < $2::text::timestamptz
    RETURNING owner, kind, bytes
), records AS (
    DELETE FROM {{schema}}.turn_records r USING dropped
    WHERE r.conversation_id = $1 AND r.owner = dropped.owner
), staging AS (
    DELETE FROM {{schema}}.record_staging s USING dropped
    WHERE s.conversation_id = $1 AND s.owner = dropped.owner
)
SELECT count(*) FILTER (WHERE kind = 'write') AS drafts,
       count(*) FILTER (WHERE kind = 'restore') AS restores,
       CASE WHEN coalesce(min(bytes), 0) >= 0 THEN coalesce(sum(bytes), 0)::text END AS freed
FROM dropped
