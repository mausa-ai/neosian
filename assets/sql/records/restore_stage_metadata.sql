-- A header carries counters beside its immutable wire body and digest.
-- Repeats compare only the original metadata, using the owner's primary key.
WITH wanted AS (
    SELECT * FROM jsonb_to_recordset($3::text::jsonb)
        AS w(ord bigint, item text, body jsonb, digest text, repeated boolean)
), added AS (
    INSERT INTO {{schema}}.record_staging (conversation_id, owner, ord, item, body, digest)
    SELECT $1, $2, ord, item,
           CASE WHEN item = 'turn' THEN body || '{"received_records":0,"received_bytes":0}'::jsonb
                ELSE body END,
           decode(digest, 'hex')
    FROM wanted WHERE NOT repeated
)
SELECT NOT EXISTS (
    SELECT 1 FROM wanted w
    LEFT JOIN LATERAL (
        SELECT item, digest, body FROM {{schema}}.record_staging
        WHERE (conversation_id, owner, ord) = ($1, $2, w.ord) LIMIT 1
    ) s ON true
    WHERE w.repeated AND (
        s.item IS DISTINCT FROM w.item OR s.digest IS DISTINCT FROM decode(w.digest, 'hex')
        OR (CASE WHEN w.item = 'turn' THEN s.body - 'received_records' - 'received_bytes'
                 ELSE s.body END) IS DISTINCT FROM w.body)
) AS matches
