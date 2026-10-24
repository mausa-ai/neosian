
        WITH current AS (
            SELECT version, neosian_format FROM {{schema}}.memories
            WHERE scope = %(scope)s AND path = %(path)s
        ), gate AS (
            SELECT
                (SELECT count(*) FROM current) > 0 AS existed,
                COALESCE((SELECT neosian_format FROM current), %(format)s)
                    <= %(format)s AS format_ok,
                (%(expected)s::integer IS NULL
                 OR (SELECT version FROM current)
                    IS NOT DISTINCT FROM %(expected)s::integer) AS version_ok
        ), next AS (
            SELECT GREATEST(
                COALESCE((SELECT max(version) FROM {{schema}}.memory_versions
                          WHERE scope = %(scope)s AND path = %(path)s), 0),
                COALESCE((SELECT version FROM current), 0)
            ) + 1 AS version
        ), vrow AS (
            INSERT INTO {{schema}}.memory_versions
                (scope, path, version, action, content, actor, created_at,
                 neosian_format)
            SELECT %(scope)s, %(path)s, next.version,
                   CASE WHEN g.existed THEN 'modified' ELSE 'created' END,
                   %(content)s, %(actor)s, %(now)s, %(format)s
            FROM next, gate AS g
            WHERE g.format_ok AND g.version_ok
            RETURNING version
        ), doc AS (
            INSERT INTO {{schema}}.memories
                (scope, path, content, version, created_at, updated_at,
                 actor, redacted, neosian_format)
            SELECT %(scope)s, %(path)s, %(content)s, vrow.version,
                   %(now)s, %(now)s, %(actor)s, false, %(format)s
            FROM vrow
            ON CONFLICT (scope, path) DO UPDATE SET
                content = EXCLUDED.content,
                version = EXCLUDED.version,
                updated_at = EXCLUDED.updated_at,
                actor = EXCLUDED.actor,
                redacted = false,
                neosian_format = EXCLUDED.neosian_format
            RETURNING version, created_at, updated_at, extra
        )
        SELECT g.existed, g.format_ok, g.version_ok,
               (SELECT version FROM current) AS current_version,
               (SELECT neosian_format FROM current) AS current_format,
               d.version, d.created_at, d.updated_at, d.extra
        FROM gate AS g LEFT JOIN doc AS d ON true
