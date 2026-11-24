-- For a whole-database relevance search, find matching owners once with
-- the optional text indexes, then intersect with visible turn mappings.
(m.conversation_id, m.owner, m.part) IN (
    SELECT DISTINCT found.conversation_id, found.owner, found.part
    FROM {{patterns}} (
        SELECT r.conversation_id, r.owner, r.part FROM searchable r
        WHERE {{match}}
        {{barrier}}
    ) AS found
)
