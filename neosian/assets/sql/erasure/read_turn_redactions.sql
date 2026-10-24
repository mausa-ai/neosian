
        SELECT conversation_id, turns, actor, created_at
        FROM {{schema}}.turn_redactions
        WHERE (%(all)s::boolean OR conversation_id = ANY(%(ids)s::text[]))
          AND (%(since)s::timestamptz IS NULL OR created_at >= %(since)s)
        ORDER BY created_at DESC, conversation_id COLLATE "C" DESC, id DESC
        LIMIT %(limit)s
