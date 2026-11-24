UPDATE {{schema}}.record_drafts SET position = $3, bytes = $4, touched_at = clock_timestamp()
WHERE conversation_id = $1 AND owner = $2
