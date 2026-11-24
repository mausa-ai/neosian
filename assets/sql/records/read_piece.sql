SELECT r.seq - r.chunk AS first
FROM {{schema}}.record_turns m
JOIN {{schema}}.turn_records r
    ON (r.conversation_id, r.owner, r.part) = (m.conversation_id, m.owner, m.part)
WHERE m.conversation_id = $1 AND m.turn = $2 AND r.seq = $3::bigint
