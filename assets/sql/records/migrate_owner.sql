-- A normal identity INSERT needs only the existing table privileges. The
-- empty name is outside the draft grammar and the transaction retires it
-- before publishing, so no actor can claim or observe this temporary owner.
INSERT INTO {{schema}}.record_drafts
    (conversation_id, kind, name, state, opened_at, touched_at, position, bytes)
VALUES ($1, 'write', '', 'open', statement_timestamp(), statement_timestamp(), '{}'::jsonb, 0)
RETURNING owner
