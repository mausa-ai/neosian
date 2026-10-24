SELECT format, stream, sequence, floor FROM {{schema}}.memory_change_streams
WHERE scope = %(scope)s
