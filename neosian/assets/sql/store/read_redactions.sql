
        SELECT path, actor, created_at, count
        FROM {{schema}}.memory_redactions
        WHERE scope = %(scope)s
          AND (%(since)s::timestamptz IS NULL OR created_at >= %(since)s)
        ORDER BY created_at DESC, id DESC
        LIMIT %(limit)s
