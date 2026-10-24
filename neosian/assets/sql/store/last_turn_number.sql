
        SELECT COALESCE(MAX(turn), 0)
        FROM {{schema}}.turns WHERE conversation_id = %(conversation_id)s
