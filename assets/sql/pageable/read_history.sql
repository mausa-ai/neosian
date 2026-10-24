
        SELECT path, version, action, content, actor, created_at, redacted,
               neosian_format
        FROM {{schema}}.memory_versions
        WHERE scope = %(scope)s
          AND (%(since)s::timestamptz IS NULL OR created_at >= %(since)s)
          AND (
            %(at)s::timestamptz IS NULL
            OR created_at < %(at)s
            OR (created_at = %(at)s AND (
                path COLLATE "C" > %(path)s::text
                OR (path = %(path)s AND version < %(version)s::integer)
            ))
          )
        ORDER BY created_at DESC, path COLLATE "C", version DESC
        LIMIT %(limit)s
