
        SELECT path, version, action, content, actor, created_at, redacted,
               neosian_format
        FROM {{schema}}.memory_versions
        WHERE scope = %(scope)s AND path = %(path)s
        ORDER BY version DESC
        LIMIT %(limit)s
