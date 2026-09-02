-- PostgresStore reference schema (DESIGN §8, §9.2; phase N3).
--
-- Idempotent: every CREATE is IF NOT EXISTS and the version stamp is an
-- upsert, so re-applying is a no-op and future releases extend it with
-- equally idempotent statements. {{schema}} and {{version}} are rendered
-- by neosian before execution (a validated, double-quoted identifier and
-- SCHEMA_VERSION — never end-user input).
--
-- RLS-friendly by construction: the ownership key (scope on every memory
-- row, conversation_id on every turn row) is present on each row, so a
-- policy needs no join. Example, deliberately not enabled:
--
--     ALTER TABLE {{schema}}.memories ENABLE ROW LEVEL SECURITY;
--     CREATE POLICY memories_tenant ON {{schema}}.memories
--         USING (scope = current_setting('neosian.scope', true));

CREATE SCHEMA IF NOT EXISTS {{schema}};

-- The DDL generation this schema holds: one row, monotonic. IF NOT EXISTS
-- cannot add a column, so a later generation arrives as new idempotent
-- statements and a higher number here (postgres/schema.py SCHEMA_VERSION).
CREATE TABLE IF NOT EXISTS {{schema}}.neosian_schema (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version   integer NOT NULL
);

INSERT INTO {{schema}}.neosian_schema (version) VALUES ({{version}})
    ON CONFLICT (singleton) DO UPDATE
    SET version = GREATEST(neosian_schema.version, EXCLUDED.version);

-- Memory (DESIGN §8) ---------------------------------------------------

-- A deleted document is a removed row; its tombstone lives in
-- memory_versions. `extra` preserves unknown substrate keys (C6).
-- `search` is the dormant FTS escape hatch — populated, indexed, and
-- reachable by raw SQL only; no API surface until a tenant's memory
-- outgrows index-scan-plus-grep. left() bounds the input so a multi-MB
-- document can never overflow the 1 MB tsvector limit.
CREATE TABLE IF NOT EXISTS {{schema}}.memories (
    scope          text        NOT NULL,
    path           text        NOT NULL,
    content        text        NOT NULL,
    version        integer     NOT NULL,
    created_at     timestamptz NOT NULL,
    updated_at     timestamptz NOT NULL,
    actor          text,
    redacted       boolean     NOT NULL DEFAULT false,
    neosian_format integer     NOT NULL DEFAULT 1,
    extra          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    search         tsvector GENERATED ALWAYS AS
                       (to_tsvector('simple', left(content, 100000))) STORED,
    PRIMARY KEY (scope, path)
);

CREATE INDEX IF NOT EXISTS memories_prefix
    ON {{schema}}.memories (scope, path text_pattern_ops);

CREATE INDEX IF NOT EXISTS memories_search
    ON {{schema}}.memories USING gin (search);

-- Full content on every row (C5); the primary key doubles as the
-- never-reuse-a-version guard that concurrent writers retry against.
CREATE TABLE IF NOT EXISTS {{schema}}.memory_versions (
    scope          text        NOT NULL,
    path           text        NOT NULL,
    version        integer     NOT NULL,
    action         text        NOT NULL
                   CHECK (action IN ('created', 'modified', 'deleted')),
    content        text        NOT NULL,
    actor          text,
    created_at     timestamptz NOT NULL,
    redacted       boolean     NOT NULL DEFAULT false,
    neosian_format integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (scope, path, version)
);

-- Erasure trail (the redactions.jsonl parity): who redacted what, when.
-- Store-local audit, invisible to the MemoryStore contract.
CREATE TABLE IF NOT EXISTS {{schema}}.memory_redactions (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope      text        NOT NULL,
    path       text,
    actor      text,
    count      integer     NOT NULL,
    created_at timestamptz NOT NULL
);

-- Conversation (DESIGN §9.2) -------------------------------------------

CREATE TABLE IF NOT EXISTS {{schema}}.conversations (
    conversation_id text        PRIMARY KEY,
    created_at      timestamptz NOT NULL
);

-- CS3: UNIQUE(conversation_id, turn) is the gapless-numbering guard;
-- appenders compute COALESCE(MAX(turn),0)+1 and retry on collision.
-- FKs are deferred so the parent upsert in a sibling CTE always lands
-- first within the same statement.
CREATE TABLE IF NOT EXISTS {{schema}}.turns (
    conversation_id text        NOT NULL
        REFERENCES {{schema}}.conversations (conversation_id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    turn            integer     NOT NULL,
    messages        jsonb       NOT NULL,
    created_at      timestamptz NOT NULL,
    neosian_format  integer     NOT NULL DEFAULT 1,
    PRIMARY KEY (conversation_id, turn)
);

-- `id` realizes insertion order: read order is (turn, span, id), the
-- tie-to-last-appended rule of §9.6. No unique key by design — entries
-- are never deleted and overlaps render deterministically.
CREATE TABLE IF NOT EXISTS {{schema}}.projections (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id text    NOT NULL
        REFERENCES {{schema}}.conversations (conversation_id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    turn            integer NOT NULL,
    kind            text    NOT NULL CHECK (kind IN ('log', 'digest', 'epoch')),
    text            text    NOT NULL,
    span            integer NOT NULL DEFAULT 1,
    neosian_format  integer NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS projections_order
    ON {{schema}}.projections (conversation_id, turn, span, id);
