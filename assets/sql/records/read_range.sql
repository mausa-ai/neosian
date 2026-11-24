-- Fix the mapping and both ends of the interval, so even a generic plan
-- that sorts only visits the requested record bodies.
SELECT json_build_object(
           'seq', r.seq, 'message', r.message, 'role', r.role, 'kind', r.kind,
           'content', r.content, 'block', r.block, 'call', r.call,
           'call_id', r.call_id, 'name', r.name, 'chunk', r.chunk,
           'size', r.size, 'text', r.text
       ) AS body, r.digest, r.neosian_format
FROM {{schema}}.turn_records r
WHERE r.conversation_id = $1 AND r.seq >= $3::bigint AND r.seq < $4::bigint
  AND (r.owner, r.part) = (
      SELECT m.owner, m.part FROM {{schema}}.record_turns m
      WHERE m.conversation_id = $1 AND m.turn = $2
  )
ORDER BY r.seq
