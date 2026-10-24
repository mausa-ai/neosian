
        WITH targets AS (
            SELECT path FROM {{schema}}.memories
            WHERE scope = %(scope)s
              AND (%(path)s::text IS NULL OR path = %(path)s)
            UNION
            SELECT path FROM {{schema}}.memory_versions
            WHERE scope = %(scope)s
              AND (%(path)s::text IS NULL OR path = %(path)s)
        ), doc_update AS (
            UPDATE {{schema}}.memories AS m SET content = '', redacted = true
            WHERE m.scope = %(scope)s
              AND m.path IN (SELECT path FROM targets)
            RETURNING 1
        ), version_update AS (
            UPDATE {{schema}}.memory_versions AS v SET content = '', redacted = true
            WHERE v.scope = %(scope)s
              AND v.path IN (SELECT path FROM targets)
            RETURNING 1
        ), trail AS (
            INSERT INTO {{schema}}.memory_redactions
                (scope, path, actor, count, created_at)
            SELECT %(scope)s, %(path)s, %(actor)s,
                   (SELECT count(*) FROM targets), %(now)s
            WHERE (SELECT count(*) FROM targets) > 0
            RETURNING 1
        )
        SELECT (SELECT count(*) FROM targets) AS matched
