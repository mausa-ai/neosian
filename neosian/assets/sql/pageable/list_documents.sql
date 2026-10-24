
        SELECT path, version, created_at, updated_at, redacted,
               neosian_format
        FROM {{schema}}.memories
        WHERE scope = %(scope)s AND path LIKE %(pattern)s ESCAPE '\'
          AND (%(path)s::text IS NULL OR path COLLATE "C" > %(path)s)
        ORDER BY path COLLATE "C"
        LIMIT %(limit)s
