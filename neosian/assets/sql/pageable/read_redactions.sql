
        SELECT path, actor, created_at, count, id
        FROM {{schema}}.memory_redactions
        WHERE scope = %(scope)s
          AND (%(since)s::timestamptz IS NULL OR created_at >= %(since)s)
          AND (
            %(at)s::timestamptz IS NULL
            OR (created_at, id) < (%(at)s, %(id)s::bigint)
          )
        ORDER BY created_at DESC, id DESC
        LIMIT %(limit)s
