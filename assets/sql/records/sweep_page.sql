SELECT conversation_id COLLATE "C" AS conversation_id,
       count(*) FILTER (WHERE kind = 'write') AS drafts,
       count(*) FILTER (WHERE kind = 'restore') AS restores,
       CASE WHEN min(bytes) >= 0 THEN sum(bytes)::text END AS freed
FROM {{schema}}.record_drafts
WHERE state = 'open' AND touched_at < %(cutoff)s::timestamptz
  AND conversation_id COLLATE "C" > %(after)s
GROUP BY conversation_id COLLATE "C"
ORDER BY conversation_id COLLATE "C"
LIMIT %(limit)s::integer
