SELECT t.turn,
       CASE WHEN $5::boolean AND t.neosian_format = 1 THEN NULL ELSE t.messages END AS messages,
       t.created_at, t.neosian_format, t.actor, t.redacted,
       m.records, m.bytes, t.created_at = m.created_at AS dates_match
FROM {{schema}}.turns t
LEFT JOIN {{schema}}.record_turns m
    ON (m.conversation_id, m.turn) = (t.conversation_id, t.turn)
WHERE t.conversation_id = $1 AND t.turn BETWEEN $2::bigint AND $3::bigint
ORDER BY t.turn
LIMIT $4::bigint
