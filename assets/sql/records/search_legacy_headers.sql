-- Read only the keys. Pull one selected turn's actual JSON through the
-- bounded legacy reader. Stored search_text can predate JSONB key ordering.
SELECT conversation_id, turn, (extract(epoch FROM created_at) * 1000000)::bigint AS created
FROM {{schema}}.turns
WHERE neosian_format = 1 AND NOT redacted
  AND ($1::text[] IS NULL OR conversation_id = ANY($1::text[]))
  AND ($2::text IS NULL OR created_at >= $2::text::timestamptz)
  AND ($3::text IS NULL OR created_at < $3::text::timestamptz)
ORDER BY created_at DESC, conversation_id COLLATE "C" DESC, turn DESC
