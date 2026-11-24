SELECT t.actor, CASE WHEN $3::text IS NOT NULL THEN (
    SELECT r.seq FROM {{schema}}.turn_records r
    WHERE (r.conversation_id, r.owner, r.part) = (m.conversation_id, m.owner, m.part)
      AND r.kind = 'tool_call' AND r.call_id = $3::text
    ORDER BY r.seq LIMIT 1
) END AS call_seq
FROM {{schema}}.record_turns m
JOIN {{schema}}.turns t ON (t.conversation_id, t.turn) = (m.conversation_id, m.turn)
WHERE m.conversation_id = $1 AND m.turn = $2
  AND t.neosian_format = 2 AND NOT t.redacted AND t.created_at = m.created_at
