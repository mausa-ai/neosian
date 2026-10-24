
        SELECT turn, kind, text, span, neosian_format
        FROM {{schema}}.projections
        WHERE conversation_id = %(conversation_id)s AND turn > %(after)s
        ORDER BY turn, span, id
        LIMIT %(limit)s
