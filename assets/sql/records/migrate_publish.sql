WITH header AS (
    UPDATE {{schema}}.turns SET messages = '[]'::jsonb, search_text = NULL, neosian_format = 2
    WHERE conversation_id = $1 AND turn = $2
    RETURNING created_at
), retired AS (
    DELETE FROM {{schema}}.record_drafts
    WHERE conversation_id = $1 AND owner = $3 AND kind = 'write' AND name = ''
    RETURNING owner
)
INSERT INTO {{schema}}.record_turns (conversation_id, turn, owner, part, draft, records, bytes, created_at)
SELECT $1, $2, retired.owner, $2, $4, $5, $6, header.created_at FROM header CROSS JOIN retired
