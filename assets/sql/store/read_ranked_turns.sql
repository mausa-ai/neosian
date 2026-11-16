
        SELECT t.conversation_id, t.turn, t.messages, t.created_at, t.neosian_format,
               t.actor, t.redacted
        FROM {{schema}}.turns AS t
        JOIN unnest(%(ids)s::text[], %(turns)s::integer[]) AS w (conversation_id, turn)
          ON t.conversation_id = w.conversation_id AND t.turn = w.turn
