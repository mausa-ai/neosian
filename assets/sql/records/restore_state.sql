SELECT owner, state, position, bytes FROM {{schema}}.record_drafts
WHERE conversation_id = $1 AND kind = 'restore' AND name = $2
