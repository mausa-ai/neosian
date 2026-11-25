r.conversation_id = %(conversation_id)s AND (
    %(full)s::boolean OR r.turn IN (
        SELECT t.turn FROM {{schema}}.turns t
        WHERE t.conversation_id = %(conversation_id)s
          AND (%(through)s::int IS NULL OR t.turn <= %(through)s)
          AND (%(all)s::boolean OR t.turn = ANY(%(turns)s::int[]))
    )
)
