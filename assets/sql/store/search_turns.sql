
        SELECT conversation_id, turn, messages, created_at, neosian_format, actor,
               redacted
        FROM {{schema}}.turns
        WHERE search_text ILIKE ALL(%(patterns)s::text[])
          AND (%(all)s::boolean OR conversation_id = ANY(%(ids)s::text[]))
        ORDER BY created_at DESC, conversation_id COLLATE "C" DESC, turn DESC
        LIMIT %(limit)s
