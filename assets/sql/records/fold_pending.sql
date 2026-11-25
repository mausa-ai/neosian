SELECT count(*) FILTER (WHERE kind = 'write') AS drafts,
       count(*) FILTER (WHERE kind = 'restore') AS restores,
       NOT EXISTS (
           SELECT FROM {{schema}}.record_turns m
           LEFT JOIN {{schema}}.turns t USING (conversation_id, turn)
           WHERE m.conversation_id = $1
             AND (t.turn IS NULL OR EXISTS (
                 SELECT FROM {{schema}}.record_drafts d
                 WHERE d.conversation_id = $1 AND d.owner = m.owner AND d.state = 'open'
             ))
       ) AS valid
FROM {{schema}}.record_drafts WHERE conversation_id = $1 AND state = 'open'
