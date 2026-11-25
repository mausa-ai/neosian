-- A fold visits empty parents and conversations holding only pending work.
SELECT conversation_id COLLATE "C" AS conversation_id FROM {{schema}}.conversations
WHERE cardinality(%(named)s::text[]) = 0 OR conversation_id = ANY(%(named)s::text[])
UNION
SELECT conversation_id COLLATE "C" FROM {{schema}}.record_drafts
WHERE cardinality(%(named)s::text[]) = 0 OR conversation_id = ANY(%(named)s::text[])
ORDER BY conversation_id
