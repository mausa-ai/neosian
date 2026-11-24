-- Optional records extension 1 for reference schema 4.
-- Pause writers, apply as the schema owner, then restart compatible writers.
BEGIN;

CREATE TABLE IF NOT EXISTS {{schema}}.neosian_records (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version   integer NOT NULL
);
INSERT INTO {{schema}}.neosian_records (version) VALUES (1)
    ON CONFLICT (singleton) DO NOTHING;

-- Keep records under their writer's owner and part even after publication.
CREATE TABLE IF NOT EXISTS {{schema}}.record_drafts (
    owner           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id text        NOT NULL,
    kind            text        NOT NULL CHECK (kind IN ('write', 'restore')),
    name            text        NOT NULL,
    state           text        NOT NULL CHECK (state IN ('open', 'committed')),
    actor           text,
    turn_bytes      bigint,
    opened_at       timestamptz NOT NULL,
    touched_at      timestamptz NOT NULL,
    position        jsonb       NOT NULL,
    bytes           bigint      NOT NULL,
    UNIQUE (conversation_id, kind, name)
);
CREATE TABLE IF NOT EXISTS {{schema}}.turn_records (
    conversation_id text    NOT NULL,
    owner           bigint  NOT NULL,
    part            integer NOT NULL,
    seq             integer NOT NULL,
    message         integer NOT NULL,
    role            text    NOT NULL,
    kind            text    NOT NULL,
    content         text,
    block           integer,
    call            integer,
    call_id         text,
    name            text,
    chunk           integer NOT NULL,
    size            bigint  NOT NULL,
    last            boolean NOT NULL,
    text            text    NOT NULL,
    digest          bytea   NOT NULL,
    neosian_format  integer NOT NULL DEFAULT 1,
    PRIMARY KEY (conversation_id, owner, part, seq)
);

-- This mapping makes a turn visible without copying or updating its records.
CREATE TABLE IF NOT EXISTS {{schema}}.record_turns (
    conversation_id text        NOT NULL,
    turn            integer     NOT NULL,
    owner           bigint      NOT NULL,
    part            integer     NOT NULL,
    draft           text        NOT NULL,
    records         integer     NOT NULL,
    bytes           bigint      NOT NULL,
    created_at      timestamptz NOT NULL,
    PRIMARY KEY (conversation_id, turn),
    UNIQUE (conversation_id, owner, part)
);
CREATE INDEX IF NOT EXISTS record_turns_draft
    ON {{schema}}.record_turns (conversation_id, draft);

CREATE TABLE IF NOT EXISTS {{schema}}.record_staging (
    conversation_id text   NOT NULL,
    owner           bigint NOT NULL,
    ord             bigint NOT NULL,
    item            text   NOT NULL CHECK (item IN ('turn', 'projection', 'redaction')),
    body            jsonb  NOT NULL,
    digest          bytea  NOT NULL,
    PRIMARY KEY (conversation_id, owner, ord)
);

COMMIT;
