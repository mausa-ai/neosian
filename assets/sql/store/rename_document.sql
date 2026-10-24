
        WITH src AS (
            SELECT content, version, redacted, neosian_format, extra
            FROM {{schema}}.memories
            WHERE scope = %(scope)s AND path = %(src)s
        ), dst AS (
            SELECT version FROM {{schema}}.memories
            WHERE scope = %(scope)s AND path = %(dst)s
        ), gate AS (
            SELECT
                (SELECT count(*) FROM src) > 0 AS src_exists,
                (SELECT count(*) FROM dst) > 0 AS dst_exists,
                COALESCE((SELECT neosian_format FROM src), %(format)s)
                    BETWEEN 1 AND %(format)s AS format_ok
        ), go AS (
            SELECT src_exists AND NOT dst_exists AND format_ok AS ok FROM gate
        ), src_next AS (
            SELECT GREATEST(
                COALESCE((SELECT max(version) FROM {{schema}}.memory_versions
                          WHERE scope = %(scope)s AND path = %(src)s), 0),
                COALESCE((SELECT version FROM src), 0)
            ) + 1 AS version
        ), dst_next AS (
            SELECT COALESCE((SELECT max(version) FROM {{schema}}.memory_versions
                             WHERE scope = %(scope)s AND path = %(dst)s), 0)
                   + 1 AS version
        ), src_row AS (
            INSERT INTO {{schema}}.memory_versions
                (scope, path, version, action, content, actor, created_at,
                 redacted, neosian_format)
            SELECT %(scope)s, %(src)s, src_next.version, 'deleted',
                   src.content, %(actor)s, %(now)s, src.redacted, %(format)s
            FROM src_next, src
            WHERE (SELECT ok FROM go)
            RETURNING version
        ), dst_row AS (
            INSERT INTO {{schema}}.memory_versions
                (scope, path, version, action, content, actor, created_at,
                 redacted, neosian_format)
            SELECT %(scope)s, %(dst)s, dst_next.version, 'created',
                   src.content, %(actor)s, %(now)s, src.redacted, %(format)s
            FROM dst_next, src
            WHERE (SELECT ok FROM go)
            RETURNING version
        ), doc AS (
            INSERT INTO {{schema}}.memories
                (scope, path, content, version, created_at, updated_at,
                 actor, redacted, neosian_format, extra)
            SELECT %(scope)s, %(dst)s, src.content, dst_row.version,
                   %(now)s, %(now)s, %(actor)s, src.redacted, %(format)s,
                   src.extra
            FROM dst_row, src
            RETURNING content, version, created_at, updated_at, redacted,
                      extra
        ), removal AS (
            DELETE FROM {{schema}}.memories AS m
            WHERE m.scope = %(scope)s AND m.path = %(src)s
              AND EXISTS (SELECT 1 FROM doc)
            RETURNING 1
        )
        SELECT g.src_exists, g.dst_exists, g.format_ok,
               (SELECT neosian_format FROM src) AS current_format,
               d.content, d.version, d.created_at, d.updated_at,
               d.redacted, d.extra
        FROM gate AS g LEFT JOIN doc AS d ON true
