-- Re-read only the overlapping records. A replay must also refuse a future
-- stored format or a damaged body whose digest no longer matches.
SELECT json_build_object(
           'seq', r.seq, 'message', r.message, 'role', r.role, 'kind', r.kind,
           'content', r.content, 'block', r.block, 'call', r.call,
           'call_id', r.call_id, 'name', r.name, 'chunk', r.chunk,
           'size', r.size, 'text', r.text
       ) AS body, r.digest, r.neosian_format
FROM {{schema}}.turn_records AS r
WHERE r.conversation_id = $1 AND r.owner = $2 AND r.part = 0
  AND r.seq >= $3 AND r.seq <= $4
ORDER BY r.seq
