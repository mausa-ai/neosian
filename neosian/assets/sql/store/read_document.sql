
        SELECT content, version, created_at, updated_at, actor, redacted,
               neosian_format, extra
        FROM {{schema}}.memories
        WHERE scope = %(scope)s AND path = %(path)s
