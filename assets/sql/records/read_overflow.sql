-- After reading the declared count, check for a surplus row without fetching text.
SELECT EXISTS (
    SELECT 1 FROM {{schema}}.turn_records r
    WHERE r.conversation_id = $1 AND r.seq >= $3::bigint
      AND (r.owner, r.part) = (
          SELECT m.owner, m.part FROM {{schema}}.record_turns m
          WHERE m.conversation_id = $1 AND m.turn = $2
      )
)
