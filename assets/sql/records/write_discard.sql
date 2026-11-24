WITH removed AS (
    DELETE FROM {{schema}}.turn_records WHERE conversation_id = $1 AND owner = $2
)
DELETE FROM {{schema}}.record_drafts WHERE conversation_id = $1 AND owner = $2
