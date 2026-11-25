-- Check the copied header and its count of retained pages before the fold
-- can retire the mapping or its original record rows.
SELECT EXISTS (
    SELECT FROM {{schema}}.record_seals h
    JOIN {{schema}}.record_turns m USING (conversation_id, turn)
    JOIN {{schema}}.turns t USING (conversation_id, turn)
    WHERE h.conversation_id = $1 AND h.turn = $2 AND m.owner = $3 AND m.part = 0
      AND (h.records, h.bytes, h.draft, h.created_at) = (m.records, m.bytes, m.draft, m.created_at)
      AND h.actor IS NOT DISTINCT FROM t.actor AND h.pages_seen = (h.records - 1) / 32 + 1
)
