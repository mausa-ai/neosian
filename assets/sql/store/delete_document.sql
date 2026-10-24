
        WITH current AS (
            SELECT content, version, redacted, neosian_format
            FROM {{schema}}.memories
            WHERE scope = %(scope)s AND path = %(path)s
        ), gate AS (
            SELECT
                (SELECT count(*) FROM current) > 0 AS existed,
                COALESCE((SELECT neosian_format FROM current), %(format)s)
                    BETWEEN 1 AND %(format)s AS format_ok
        ), next AS (
            SELECT GREATEST(
                COALESCE((SELECT max(version) FROM {{schema}}.memory_versions
                          WHERE scope = %(scope)s AND path = %(path)s), 0),
                COALESCE((SELECT version FROM current), 0)
            ) + 1 AS version
        ), vrow AS (
            INSERT INTO {{schema}}.memory_versions
                (scope, path, version, action, content, actor, created_at,
                 redacted, neosian_format)
            SELECT %(scope)s, %(path)s, next.version, 'deleted',
                   c.content, %(actor)s, %(now)s, c.redacted, %(format)s
            FROM next, current AS c, gate AS g
            WHERE g.existed AND g.format_ok
            RETURNING version
        ), removal AS (
            DELETE FROM {{schema}}.memories AS m
            WHERE m.scope = %(scope)s AND m.path = %(path)s
              AND EXISTS (SELECT 1 FROM vrow)
            RETURNING 1
        )
        SELECT g.existed, g.format_ok,
               (SELECT neosian_format FROM current) AS current_format
        FROM gate AS g
