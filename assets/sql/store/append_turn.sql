
        WITH parent AS (
            INSERT INTO {{schema}}.conversations (conversation_id, created_at)
            VALUES (%(conversation_id)s, %(now)s)
            ON CONFLICT (conversation_id) DO NOTHING
        ), next AS (
            SELECT COALESCE(MAX(turn), 0) + 1 AS turn
            FROM {{schema}}.turns WHERE conversation_id = %(conversation_id)s
        )
        INSERT INTO {{schema}}.turns
            (conversation_id, turn, messages, created_at, neosian_format, actor,
             search_text)
        SELECT %(conversation_id)s, next.turn, %(messages)s::jsonb,
               %(now)s, %(format)s, %(actor)s, %(search_text)s
        FROM next
        RETURNING turn
