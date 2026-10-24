
        WITH gate AS (
            SELECT NOT EXISTS (SELECT 1 FROM {{schema}}.turns
                               WHERE conversation_id = %(conversation_id)s)
               AND NOT EXISTS (SELECT 1 FROM {{schema}}.projections
                               WHERE conversation_id = %(conversation_id)s) AS empty
        ), parent AS (
            INSERT INTO {{schema}}.conversations (conversation_id, created_at)
            SELECT %(conversation_id)s, %(now)s
            WHERE (SELECT empty FROM gate)
            ON CONFLICT (conversation_id) DO NOTHING
        ), rows AS (
            INSERT INTO {{schema}}.turns
                (conversation_id, turn, messages, created_at, neosian_format,
                 actor, search_text, redacted)
            SELECT %(conversation_id)s, (t.value ->> 'turn')::integer,
                   t.value -> 'messages', (t.value ->> 'created_at')::timestamptz,
                   %(format)s, t.value ->> 'actor', t.value ->> 'search_text',
                   COALESCE((t.value ->> 'redacted')::boolean, false)
            FROM jsonb_array_elements(%(turns)s::jsonb) AS t(value)
            WHERE (SELECT empty FROM gate)
            RETURNING 1
        ), acts AS (
            INSERT INTO {{schema}}.turn_redactions (conversation_id, turns, actor, created_at)
            SELECT %(conversation_id)s,
                   ARRAY(SELECT n.value::integer
                         FROM jsonb_array_elements_text(a.value -> 'turns')
                              WITH ORDINALITY AS n(value, ord)
                         ORDER BY n.ord),
                   a.value ->> 'actor', (a.value ->> 'created_at')::timestamptz
            FROM jsonb_array_elements(%(redactions)s::jsonb)
                 WITH ORDINALITY AS a(value, ord)
            WHERE (SELECT empty FROM gate)
            ORDER BY a.ord
            RETURNING 1
        ), entries AS (
            INSERT INTO {{schema}}.projections
                (conversation_id, turn, kind, text, span, neosian_format)
            SELECT %(conversation_id)s, (p.value ->> 'turn')::integer,
                   p.value ->> 'kind', p.value ->> 'text',
                   (p.value ->> 'span')::integer, %(format)s
            FROM jsonb_array_elements(%(projections)s::jsonb)
                 WITH ORDINALITY AS p(value, ord)
            WHERE (SELECT empty FROM gate)
            ORDER BY p.ord
            RETURNING 1
        )
        SELECT empty FROM gate
