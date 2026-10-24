SELECT path, content, version, created_at, updated_at, actor, redacted,
       neosian_format, extra
FROM {{schema}}.memories
WHERE scope = %(scope)s AND path LIKE %(pattern)s ESCAPE '\'
  AND path COLLATE "C" > %(after)s COLLATE "C"
  AND path NOT LIKE 'sessions/%' AND path NOT LIKE 'messages/%'
  AND path NOT LIKE 'message-links/%' AND path <> 'handoff'
ORDER BY path COLLATE "C"
