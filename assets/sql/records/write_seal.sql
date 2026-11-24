-- Sample at publication, after all of this request's records have landed.
-- No record row is touched. The parent and both header rows share one instant.
WITH moment AS MATERIALIZED (SELECT clock_timestamp() AS at), parent AS (
    INSERT INTO {{schema}}.conversations (conversation_id, created_at)
    SELECT $1, at FROM moment ON CONFLICT (conversation_id) DO NOTHING RETURNING 1
), next AS (
    SELECT coalesce(max(turn), 0)::bigint + 1 AS turn
    FROM {{schema}}.turns WHERE conversation_id = $1
), header AS (
    INSERT INTO {{schema}}.turns
        (conversation_id, turn, messages, created_at, neosian_format, actor, search_text)
    SELECT $1, next.turn::integer, '[]'::jsonb, moment.at, 2, $3, NULL
    FROM next, moment WHERE next.turn <= 2147483647 RETURNING turn, created_at
), mapping AS (
    INSERT INTO {{schema}}.record_turns
        (conversation_id, turn, owner, part, draft, records, bytes, created_at)
    SELECT $1, header.turn, $2, 0, $4, $5, $6, header.created_at FROM header
), closed AS (
    DELETE FROM {{schema}}.record_drafts WHERE conversation_id = $1 AND owner = $2
)
SELECT turn, created_at, EXISTS(SELECT 1 FROM parent) AS created FROM header
