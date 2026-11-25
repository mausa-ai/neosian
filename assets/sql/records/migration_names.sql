SELECT DISTINCT name COLLATE "C" AS conversation_id
FROM unnest(%(named)s::text[]) AS requested(name)
WHERE EXISTS (SELECT FROM {{schema}}.conversations WHERE conversation_id = requested.name)
   OR EXISTS (SELECT FROM {{schema}}.turns WHERE conversation_id = requested.name)
   OR EXISTS (SELECT FROM {{schema}}.projections WHERE conversation_id = requested.name)
   OR EXISTS (SELECT FROM {{schema}}.record_drafts d
              WHERE d.conversation_id = requested.name AND d.kind = 'write' AND d.state = 'open')
ORDER BY conversation_id
