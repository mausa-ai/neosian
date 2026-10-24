WITH removed AS (
    DELETE FROM {{schema}}.memory_changes
    WHERE scope = %(scope)s AND sequence > %(floor)s AND sequence <= %(through)s
    RETURNING event, sequence
), advance AS (
    UPDATE {{schema}}.memory_change_streams SET floor = %(through)s
    WHERE scope = %(scope)s RETURNING floor
)
SELECT event FROM removed ORDER BY sequence
