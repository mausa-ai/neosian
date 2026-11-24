EXISTS (
    SELECT 1 FROM searchable r
    WHERE (r.conversation_id, r.owner, r.part) = (m.conversation_id, m.owner, m.part)
      AND ({{match}})
)
