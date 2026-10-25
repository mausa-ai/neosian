SELECT path,
       CASE WHEN octet_length(content)::bigint + octet_length(extra::text)::bigint <= %(bytes)s
            THEN content ELSE NULL END AS content,
       version, created_at, updated_at, actor, redacted, neosian_format,
       CASE WHEN octet_length(content)::bigint + octet_length(extra::text)::bigint <= %(bytes)s
            THEN extra ELSE NULL END AS extra,
       octet_length(content)::bigint + octet_length(extra::text)::bigint AS record_bytes
FROM {{schema}}.memories
WHERE scope = %(scope)s AND path LIKE %(pattern)s ESCAPE '\'
  AND path COLLATE "C" > %(after)s COLLATE "C"
  AND path NOT LIKE 'sessions/%' AND path NOT LIKE 'messages/%'
  AND path NOT LIKE 'message-links/%' AND path <> 'handoff'
ORDER BY path COLLATE "C"
