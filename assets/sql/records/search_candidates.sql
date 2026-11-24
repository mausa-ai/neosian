SELECT r.conversation_id, t.turn, r.seq,
       (extract(epoch FROM t.created_at) * 1000000)::bigint AS created,
       (m.created_at = t.created_at AND t.turn > 0 AND m.bytes >= 0
        AND t.messages = '[]'::jsonb AND r.seq >= 0 AND r.seq < m.records) AS valid
       {{facts}}
FROM {{schema}}.turn_records r
JOIN {{schema}}.record_turns m
  ON (m.conversation_id, m.owner, m.part) = (r.conversation_id, r.owner, r.part)
JOIN {{schema}}.turns t
  ON (t.conversation_id, t.turn) = (m.conversation_id, m.turn)
WHERE t.neosian_format = 2 AND NOT t.redacted
  AND ($1::text[] IS NULL OR r.conversation_id = ANY($1::text[]))
  AND r.kind = ANY($2::text[])
  AND ($3::text IS NULL OR t.created_at >= $3::text::timestamptz)
  AND ($4::text IS NULL OR t.created_at < $4::text::timestamptz)
  AND ({{membership}})
{{order}}
LIMIT $5::bigint
