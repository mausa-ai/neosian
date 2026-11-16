
        SELECT conversation_id, turn, created_at, neosian_format, search_text
        FROM {{schema}}.turns
        WHERE search_text ILIKE ANY(%(patterns)s::text[])
          AND (%(all)s::boolean OR conversation_id = ANY(%(ids)s::text[]))
          AND (%(since)s::timestamptz IS NULL OR created_at >= %(since)s)
          AND (%(before)s::timestamptz IS NULL OR created_at < %(before)s)
