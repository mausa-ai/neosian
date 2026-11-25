-- The conversation lock precedes this row lock. Return only metadata so a
-- large legacy document never crosses the response ceiling in one row.
SELECT t.turn, t.neosian_format, t.redacted, m.turn IS NOT NULL AS mapped,
       CASE WHEN t.redacted OR t.neosian_format = 2 THEN t.messages = '[]'::jsonb END AS empty,
       m.records > 0 AND m.bytes >= 0 AND m.created_at = t.created_at
           AND m.owner > 0 AND m.part IN (0, t.turn) AS valid
FROM {{schema}}.turns t
LEFT JOIN {{schema}}.record_turns m ON (m.conversation_id, m.turn) = (t.conversation_id, t.turn)
WHERE t.conversation_id = $1 AND t.turn > $2::bigint AND t.turn <= $3::bigint
ORDER BY t.turn LIMIT 1 FOR UPDATE OF t
