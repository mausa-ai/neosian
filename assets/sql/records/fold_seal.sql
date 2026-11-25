-- Only a real part-zero write held the original seal identity.
INSERT INTO {{schema}}.record_seals
    (conversation_id, turn, draft, created_at, actor, records, bytes)
SELECT m.conversation_id, m.turn, m.draft, m.created_at, t.actor, m.records, m.bytes
FROM {{schema}}.record_turns m JOIN {{schema}}.turns t USING (conversation_id, turn)
WHERE m.conversation_id = $1 AND m.turn = $2 AND m.owner = $3 AND m.part = 0
RETURNING records
