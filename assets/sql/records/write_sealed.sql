SELECT m.owner, m.turn, m.records, m.bytes, m.created_at, t.actor,
       t.neosian_format, t.created_at = m.created_at AND NOT t.redacted
           AND t.messages = '[]'::jsonb AS valid
FROM {{schema}}.record_turns AS m
LEFT JOIN {{schema}}.turns AS t USING (conversation_id, turn)
WHERE m.conversation_id = $1 AND m.draft = $2 AND m.part = 0
