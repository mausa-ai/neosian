
        SELECT conversation_id, turn, messages, created_at, neosian_format, actor,
               redacted, search_text
        FROM {{schema}}.turns
        WHERE (CASE WHEN %(any)s::boolean
                    THEN search_text ILIKE ANY(%(patterns)s::text[])
                    ELSE search_text ILIKE ALL(%(patterns)s::text[]) END)
          AND (%(all)s::boolean OR conversation_id = ANY(%(ids)s::text[]))
          AND (%(since)s::timestamptz IS NULL OR created_at >= %(since)s)
          AND (%(before)s::timestamptz IS NULL OR created_at < %(before)s)
        ORDER BY created_at DESC, conversation_id COLLATE "C" DESC, turn DESC
        LIMIT %(limit)s
