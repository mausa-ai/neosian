-- Each last chunk identifies a piece. Bound the underlying primary-key
-- interval too: a bitmap scan and sort otherwise reads the entire suffix.
SELECT r.seq, r.chunk, CASE WHEN octet_length(r.kind) <= 32 THEN r.kind END AS kind, r.size
FROM {{schema}}.turn_records r
WHERE r.conversation_id = $1 AND r.seq >= $3::bigint AND r.seq < $4::bigint AND r.last
  AND (r.owner, r.part) = (
      SELECT m.owner, m.part FROM {{schema}}.record_turns m
      WHERE m.conversation_id = $1 AND m.turn = $2
  )
ORDER BY r.seq
