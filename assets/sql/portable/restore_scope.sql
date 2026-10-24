
        WITH gate AS (
            SELECT NOT EXISTS (SELECT 1 FROM {{schema}}.memories WHERE scope = %(scope)s)
               AND NOT EXISTS (SELECT 1 FROM {{schema}}.memory_versions
                               WHERE scope = %(scope)s)
               AND NOT EXISTS (SELECT 1 FROM {{schema}}.memory_redactions
                               WHERE scope = %(scope)s) AS empty
        ), rows AS (
            INSERT INTO {{schema}}.memory_versions
                (scope, path, version, action, content, actor, created_at,
                 redacted, neosian_format)
            SELECT %(scope)s, r.value ->> 'path', (r.value ->> 'version')::integer,
                   r.value ->> 'action', r.value ->> 'content', r.value ->> 'actor',
                   (r.value ->> 'created_at')::timestamptz,
                   (r.value ->> 'redacted')::boolean, %(format)s
            FROM jsonb_array_elements(%(versions)s::jsonb) AS r(value)
            WHERE (SELECT empty FROM gate)
            RETURNING 1
        ), docs AS (
            INSERT INTO {{schema}}.memories
                (scope, path, content, version, created_at, updated_at, actor,
                 redacted, neosian_format, extra)
            SELECT %(scope)s, d.value ->> 'path', d.value ->> 'content',
                   (d.value ->> 'version')::integer,
                   (d.value ->> 'created_at')::timestamptz,
                   (d.value ->> 'updated_at')::timestamptz, d.value ->> 'actor',
                   (d.value ->> 'redacted')::boolean, %(format)s,
                   COALESCE(d.value -> 'extra', '{}'::jsonb)
            FROM jsonb_array_elements(%(documents)s::jsonb) AS d(value)
            WHERE (SELECT empty FROM gate)
            RETURNING 1
        ), acts AS (
            INSERT INTO {{schema}}.memory_redactions (scope, path, actor, count, created_at)
            SELECT %(scope)s, a.value ->> 'path', a.value ->> 'actor',
                   (a.value ->> 'count')::integer,
                   (a.value ->> 'created_at')::timestamptz
            FROM jsonb_array_elements(%(redactions)s::jsonb)
                 WITH ORDINALITY AS a(value, ord)
            WHERE (SELECT empty FROM gate)
            ORDER BY a.ord
            RETURNING 1
        )
        SELECT empty FROM gate
