
        WITH matched AS (
            SELECT turn FROM {{schema}}.turns
            WHERE conversation_id = %(conversation_id)s
              AND (%(through)s::integer IS NULL OR turn <= %(through)s)
              AND (%(all)s::boolean OR turn = ANY(%(turns)s::integer[]))
        ), invalid AS (
            SELECT neosian_format FROM {{schema}}.turns
            WHERE conversation_id = %(conversation_id)s
              AND turn IN (SELECT turn FROM matched)
              AND neosian_format NOT BETWEEN 1 AND %(format)s
            UNION ALL
            SELECT neosian_format FROM {{schema}}.projections AS p
            WHERE p.conversation_id = %(conversation_id)s
              AND EXISTS (SELECT 1 FROM matched AS m
                          WHERE m.turn BETWEEN p.turn - p.span + 1 AND p.turn)
              AND neosian_format NOT BETWEEN 1 AND %(format)s
        ), gate AS (
            SELECT NOT EXISTS (SELECT 1 FROM invalid) AS format_ok
        ), blanked AS (
            UPDATE {{schema}}.turns AS t
            SET messages = '[]'::jsonb, search_text = '', redacted = true
            WHERE t.conversation_id = %(conversation_id)s
              AND t.turn IN (SELECT turn FROM matched)
              AND (SELECT format_ok FROM gate)
            RETURNING 1
        ), covering AS (
            UPDATE {{schema}}.projections AS p SET text = ''
            WHERE p.conversation_id = %(conversation_id)s
              AND EXISTS (SELECT 1 FROM matched AS m
                          WHERE m.turn BETWEEN p.turn - p.span + 1 AND p.turn)
              AND (SELECT format_ok FROM gate)
            RETURNING 1
        ), trail AS (
            INSERT INTO {{schema}}.turn_redactions (conversation_id, turns, actor, created_at)
            SELECT %(conversation_id)s,
                   (SELECT array_agg(turn ORDER BY turn) FROM matched),
                   %(actor)s, %(now)s
            WHERE (SELECT count(*) FROM matched) > 0
              AND (SELECT format_ok FROM gate)
            RETURNING 1
        )
        SELECT (SELECT count(*) FROM matched) AS matched,
               (SELECT format_ok FROM gate) AS format_ok,
               (SELECT neosian_format FROM invalid LIMIT 1) AS current_format
