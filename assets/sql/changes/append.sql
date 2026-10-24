WITH head AS (
    INSERT INTO {{schema}}.memory_change_streams (scope, format, stream, sequence, floor)
    VALUES (%(scope)s, 1, %(stream)s, %(sequence)s, %(floor)s)
    ON CONFLICT (scope) DO UPDATE SET sequence = EXCLUDED.sequence
    RETURNING scope
)
INSERT INTO {{schema}}.memory_changes (scope, sequence, event)
SELECT head.scope, (e.value ->> 'sequence')::bigint, e.value
FROM head, jsonb_array_elements(%(events)s::jsonb) AS e(value)
RETURNING sequence
