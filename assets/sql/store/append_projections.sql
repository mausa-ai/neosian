
        WITH parent AS (
            INSERT INTO {{schema}}.conversations (conversation_id, created_at)
            VALUES (%(conversation_id)s, %(now)s)
            ON CONFLICT (conversation_id) DO NOTHING
        )
        INSERT INTO {{schema}}.projections
            (conversation_id, turn, kind, text, span, neosian_format)
        SELECT %(conversation_id)s,
               (entry.value ->> 'turn')::integer,
               entry.value ->> 'kind',
               entry.value ->> 'text',
               (entry.value ->> 'span')::integer,
               %(format)s
        FROM jsonb_array_elements(%(entries)s::jsonb)
             WITH ORDINALITY AS entry(value, ord)
        ORDER BY entry.ord
