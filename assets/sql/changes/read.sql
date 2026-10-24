WITH head AS (
    SELECT format, stream, sequence, floor FROM {{schema}}.memory_change_streams
    WHERE scope = %(scope)s
), page AS (
    SELECT c.event, c.sequence FROM {{schema}}.memory_changes AS c, head
    WHERE c.scope = %(scope)s AND c.sequence > GREATEST(%(after)s::bigint, head.floor)
      AND c.sequence <= head.sequence
    ORDER BY c.sequence LIMIT %(limit)s
)
SELECT (SELECT to_jsonb(head) FROM head) AS head,
       COALESCE((SELECT jsonb_agg(event ORDER BY sequence) FROM page), '[]'::jsonb) AS events
