
        SELECT scope FROM (
            SELECT scope FROM {{schema}}.memories
            UNION SELECT scope FROM {{schema}}.memory_versions
            UNION SELECT scope FROM {{schema}}.memory_redactions
        ) AS held ORDER BY scope COLLATE "C"
