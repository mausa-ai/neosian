
        SELECT conversation_id FROM (
            SELECT conversation_id FROM {{schema}}.turns
            UNION SELECT conversation_id FROM {{schema}}.projections
        ) AS held ORDER BY conversation_id COLLATE "C"
