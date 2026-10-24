
        WITH targets AS (
            SELECT path FROM {{schema}}.memories
            WHERE scope = %(scope)s
              AND (%(path)s::text IS NULL OR path = %(path)s)
            UNION
            SELECT path FROM {{schema}}.memory_versions
            WHERE scope = %(scope)s
              AND (%(path)s::text IS NULL OR path = %(path)s)
        ), invalid AS (
            SELECT path, neosian_format FROM {{schema}}.memories
            WHERE scope = %(scope)s AND path IN (SELECT path FROM targets)
              AND neosian_format NOT BETWEEN 1 AND %(format)s
            UNION ALL
            SELECT path, neosian_format FROM {{schema}}.memory_versions
            WHERE scope = %(scope)s AND path IN (SELECT path FROM targets)
              AND neosian_format NOT BETWEEN 1 AND %(format)s
        ), gate AS (
            SELECT NOT EXISTS (SELECT 1 FROM invalid) AS format_ok
        ), doc_update AS (
            UPDATE {{schema}}.memories AS m SET content = '', redacted = true
            WHERE m.scope = %(scope)s
              AND m.path IN (SELECT path FROM targets)
              AND (SELECT format_ok FROM gate)
            RETURNING 1
        ), version_update AS (
            UPDATE {{schema}}.memory_versions AS v SET content = '', redacted = true
            WHERE v.scope = %(scope)s
              AND v.path IN (SELECT path FROM targets)
              AND (SELECT format_ok FROM gate)
            RETURNING 1
        ), trail AS (
            INSERT INTO {{schema}}.memory_redactions
                (scope, path, actor, count, created_at)
            SELECT %(scope)s, %(path)s, %(actor)s,
                   (SELECT count(*) FROM targets), %(now)s
            WHERE (SELECT count(*) FROM targets) > 0
              AND (SELECT format_ok FROM gate)
            RETURNING 1
        )
        SELECT (SELECT count(*) FROM targets) AS matched,
               (SELECT format_ok FROM gate) AS format_ok,
               (SELECT path FROM invalid ORDER BY path LIMIT 1) AS invalid_path,
               (SELECT neosian_format FROM invalid ORDER BY path LIMIT 1) AS current_format
