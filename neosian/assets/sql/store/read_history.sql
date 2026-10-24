
        SELECT path, version, action, content, actor, created_at, redacted,
               neosian_format
        FROM {{schema}}.memory_versions
        WHERE scope = %(scope)s
          AND (%(since)s::timestamptz IS NULL OR created_at >= %(since)s)
        ORDER BY created_at DESC, path COLLATE "C", version DESC
        LIMIT %(limit)s
