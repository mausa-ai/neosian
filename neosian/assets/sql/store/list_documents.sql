
        SELECT path, version, created_at, updated_at, redacted,
               neosian_format
        FROM {{schema}}.memories
        WHERE scope = %(scope)s AND path LIKE %(pattern)s ESCAPE '\'
        ORDER BY path COLLATE "C"
