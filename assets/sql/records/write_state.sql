SELECT owner, actor, turn_bytes, position, bytes
FROM {{schema}}.record_drafts
WHERE conversation_id = $1 AND kind = 'write' AND name = $2 AND state = 'open'
