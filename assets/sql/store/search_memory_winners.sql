SELECT path,
       CASE WHEN octet_length(content)::bigint + octet_length(extra::text)::bigint <= %(bytes)s
            THEN content ELSE NULL END AS content,
       version, created_at, updated_at, actor, redacted, neosian_format,
       CASE WHEN octet_length(content)::bigint + octet_length(extra::text)::bigint <= %(bytes)s
            THEN extra ELSE NULL END AS extra,
       octet_length(content)::bigint + octet_length(extra::text)::bigint AS record_bytes
FROM {{schema}}.memories
WHERE scope = %(scope)s AND path = ANY(%(paths)s::text[])
