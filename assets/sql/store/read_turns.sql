
        SELECT turn, messages, created_at, neosian_format, actor, redacted
        FROM {{schema}}.turns
        WHERE conversation_id = %(conversation_id)s AND turn > %(after)s
        ORDER BY turn
        LIMIT %(limit)s
