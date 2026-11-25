-- The caller holds the conversation lock. Pull one small header per turn
-- and retain its row lock through publication of the whole conversation.
SELECT t.turn, t.neosian_format, t.redacted, t.actor, t.created_at,
       jsonb_typeof(t.messages) = 'array' AS array_body, t.messages = '[]'::jsonb AS empty,
       m.owner, m.part, m.draft, m.records, m.bytes, m.created_at = t.created_at AS dates_match
FROM {{schema}}.turns t LEFT JOIN {{schema}}.record_turns m USING (conversation_id, turn)
WHERE t.conversation_id = $1
ORDER BY t.turn FOR UPDATE OF t
