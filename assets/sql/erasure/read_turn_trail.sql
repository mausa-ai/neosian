        SELECT id, conversation_id, turns, actor, created_at
        FROM {{schema}}.turn_redactions
        WHERE conversation_id = %(conversation_id)s AND id > %(after)s
        ORDER BY id
        LIMIT %(limit)s
