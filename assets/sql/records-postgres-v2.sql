-- Explicit upgrade from records extension 1 to 2 on reference schema 4.
-- Pause writers and run as the schema owner. Grant the writer its usual
-- table privileges on record_seals and record_seal_pages before restarting.
BEGIN;
SET LOCAL search_path = pg_catalog;

-- A second operator sees the committed marker after the first upgrade.
LOCK TABLE {{schema}}.neosian_records IN SHARE ROW EXCLUSIVE MODE;

DO $upgrade$
DECLARE
    ns regnamespace := '{{schema}}'::regnamespace;
    reference_versions integer[];
    versions integer[];
    member record;
    relation regclass;
BEGIN
    SELECT array_agg(CASE WHEN singleton THEN version END)
        INTO reference_versions FROM {{schema}}.neosian_schema;
    SELECT array_agg(CASE WHEN singleton THEN version END)
        INTO versions FROM {{schema}}.neosian_records;
    IF reference_versions IS DISTINCT FROM ARRAY[4] THEN
        RAISE EXCEPTION 'records extension 2 requires reference schema 4';
    END IF;
    IF versions IS DISTINCT FROM ARRAY[1] AND versions IS DISTINCT FROM ARRAY[2] THEN
        RAISE EXCEPTION 'expected records extension 1 or 2, found %', versions;
    END IF;

    -- Check the published fields, accepting extra operator columns and indexes.
    -- A question mark marks a nullable field in the compact catalog signatures.
    FOR member IN SELECT * FROM (VALUES
        ('neosian_schema', 'singleton:bool version:int4', 1),
        ('memories', '', 1), ('memory_versions', '', 1), ('memory_redactions', '', 1),
        ('conversations', 'conversation_id:text', 1),
        ('turns', 'conversation_id:text turn:int4 messages:jsonb created_at:timestamptz neosian_format:int4 actor:text? search_text:text? redacted:bool', 1),
        ('projections', '', 1), ('turn_redactions', '', 1),
        ('neosian_records', 'singleton:bool version:int4', 1),
        ('record_drafts', 'owner:int8 conversation_id:text kind:text name:text state:text actor:text? turn_bytes:int8? opened_at:timestamptz touched_at:timestamptz position:jsonb bytes:int8', 1),
        ('turn_records', 'conversation_id:text owner:int8 part:int4 seq:int4 message:int4 role:text kind:text content:text? block:int4? call:int4? call_id:text? name:text? chunk:int4 size:int8 last:bool text:text digest:bytea neosian_format:int4', 1),
        ('record_turns', 'conversation_id:text turn:int4 owner:int8 part:int4 draft:text records:int4 bytes:int8 created_at:timestamptz', 1),
        ('record_staging', 'conversation_id:text owner:int8 ord:int8 item:text body:jsonb digest:bytea', 1),
        ('record_seals', 'conversation_id:text turn:int4 draft:text created_at:timestamptz actor:text? records:int4 bytes:int8 pages_seen:int4', 2),
        ('record_seal_pages', 'conversation_id:text turn:int4 records:int4 page:int4 digests:bytea', 2)
    ) AS expected(name, fields, since) WHERE since <= versions[1] LOOP
        SELECT c.oid INTO relation FROM pg_class c
        WHERE c.relnamespace = ns AND c.relname = member.name AND c.relkind IN ('r','p');
        IF relation IS NULL OR EXISTS (
            SELECT FROM unnest(string_to_array(member.fields, ' ')) AS expected(field)
            WHERE NOT EXISTS (
                SELECT FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
                WHERE a.attrelid = relation AND a.attnum > 0 AND NOT a.attisdropped
                  AND t.typnamespace = 'pg_catalog'::regnamespace
                  AND a.attname || ':' || t.typname || CASE WHEN a.attnotnull THEN '' ELSE '?' END = field
            )
        ) THEN
            RAISE EXCEPTION 'incomplete records installation: relation %', member.name;
        END IF;
    END LOOP;

    -- Verify the keys, including receipt ownership and its cascading erase.
    -- Refer to column definitions rather than operator-assigned constraint names.
    FOR member IN SELECT * FROM (VALUES
        ('neosian_schema', 'PRIMARY KEY (singleton)', 1),
        ('neosian_schema', 'CHECK (singleton)', 1),
        ('turns', 'PRIMARY KEY (conversation_id, turn)', 1),
        ('neosian_records', 'PRIMARY KEY (singleton)', 1),
        ('neosian_records', 'CHECK (singleton)', 1),
        ('record_drafts', 'PRIMARY KEY (owner)', 1),
        ('record_drafts', 'UNIQUE (conversation_id, kind, name)', 1),
        ('record_drafts', $def$CHECK ((kind = ANY (ARRAY['write'::text, 'restore'::text])))$def$, 1),
        ('record_drafts', $def$CHECK ((state = ANY (ARRAY['open'::text, 'committed'::text])))$def$, 1),
        ('turn_records', 'PRIMARY KEY (conversation_id, owner, part, seq)', 1),
        ('record_turns', 'PRIMARY KEY (conversation_id, turn)', 1),
        ('record_turns', 'UNIQUE (conversation_id, owner, part)', 1),
        ('record_staging', 'PRIMARY KEY (conversation_id, owner, ord)', 1),
        ('record_staging', $def$CHECK ((item = ANY (ARRAY['turn'::text, 'projection'::text, 'redaction'::text])))$def$, 1),
        ('record_seals', 'PRIMARY KEY (conversation_id, turn)', 2),
        ('record_seals', 'UNIQUE (conversation_id, draft)', 2),
        ('record_seals', 'UNIQUE (conversation_id, turn, records)', 2),
        ('record_seals', 'CHECK ((records > 0))', 2),
        ('record_seals', 'CHECK ((bytes >= 0))', 2),
        ('record_seals', format('FOREIGN KEY (conversation_id, turn) REFERENCES %s.turns(conversation_id, turn) ON DELETE CASCADE', ns), 2),
        ('record_seal_pages', 'PRIMARY KEY (conversation_id, turn, page)', 2),
        ('record_seal_pages', 'CHECK (((page >= 0) AND (page <= ((records - 1) / 32))))', 2),
        ('record_seal_pages', 'CHECK ((octet_length(digests) = (32 * LEAST(32, (records - (page * 32))))))', 2),
        ('record_seal_pages', format('FOREIGN KEY (conversation_id, turn, records) REFERENCES %s.record_seals(conversation_id, turn, records) ON DELETE CASCADE', ns), 2)
    ) AS expected(name, definition, since) WHERE since <= versions[1] LOOP
        IF NOT EXISTS (
            -- Deparsers open relations. Filter the catalog before calling them
            -- so another schema's concurrent teardown cannot enter this check.
            WITH candidates AS MATERIALIZED (
                SELECT c.oid FROM pg_constraint c
                WHERE c.conrelid = format('%s.%I', ns, member.name)::regclass AND c.convalidated
            )
            SELECT FROM candidates WHERE pg_get_constraintdef(oid) = member.definition
        ) THEN
            RAISE EXCEPTION 'incomplete records installation: % requires %', member.name, member.definition;
        END IF;
    END LOOP;
    IF NOT EXISTS (
        SELECT FROM pg_attribute WHERE attrelid = '{{schema}}.record_drafts'::regclass
            AND attname = 'owner' AND attidentity = 'a'
    ) OR NOT EXISTS (
        WITH candidates AS MATERIALIZED (
            SELECT i.indexrelid FROM pg_index i JOIN pg_class idx ON idx.oid = i.indexrelid
            WHERE i.indrelid = '{{schema}}.record_turns'::regclass AND idx.relname = 'record_turns_draft'
              AND i.indisvalid AND i.indpred IS NULL AND i.indnkeyatts = 2
        )
        SELECT FROM candidates WHERE pg_get_indexdef(indexrelid, 1, false) = 'conversation_id'
          AND pg_get_indexdef(indexrelid, 2, false) = 'draft'
    ) THEN
        RAISE EXCEPTION 'incomplete records extension 1: missing owner identity or draft index';
    END IF;
    FOR member IN SELECT * FROM (VALUES
        ('neosian_schema', 'singleton', 'true', 1), ('neosian_records', 'singleton', 'true', 1),
        ('turn_records', 'neosian_format', '1', 1), ('record_seals', 'pages_seen', '0', 2)
    ) AS expected(name, col, value, since) WHERE since <= versions[1] LOOP
        IF NOT EXISTS (
            WITH candidates AS MATERIALIZED (
                SELECT d.adbin, d.adrelid FROM pg_attrdef d
                JOIN pg_attribute a ON (a.attrelid, a.attnum) = (d.adrelid, d.adnum)
                WHERE a.attrelid = format('%s.%I', ns, member.name)::regclass AND a.attname = member.col
            )
            SELECT FROM candidates WHERE pg_get_expr(adbin, adrelid) = member.value
        ) THEN
            RAISE EXCEPTION 'incomplete records installation: missing default on %.%', member.name, member.col;
        END IF;
    END LOOP;

    IF versions = ARRAY[2] THEN
        -- Check the enabled trigger, its exact table/event, transition table
        -- and target function. A rerun never repairs or replaces live objects.
        FOR member IN SELECT * FROM (VALUES
            ('record_seal_pages_insert', 4, 'record_seal_page_count', NULL, 'added'),
            ('record_seal_pages_delete', 8, 'record_seal_page_count', 'removed', NULL),
            ('record_seal_pages_identity', 19, 'record_seal_page_immutable', NULL, NULL)
        ) AS expected(name, event, function_name, old_table, new_table) LOOP
            IF NOT EXISTS (
                SELECT FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
                WHERE t.tgrelid = '{{schema}}.record_seal_pages'::regclass AND t.tgname = member.name
                  AND t.tgtype = member.event AND t.tgenabled IN ('O','A') AND t.tgqual IS NULL
                  AND t.tgoldtable IS NOT DISTINCT FROM member.old_table
                  AND t.tgnewtable IS NOT DISTINCT FROM member.new_table
                  AND p.pronamespace = ns AND p.proname = member.function_name
                  AND p.pronargs = 0 AND p.prorettype = 'trigger'::regtype
            ) THEN
                RAISE EXCEPTION 'incomplete records extension 2: missing or invalid %', member.name;
            END IF;
        END LOOP;
        RETURN;
    END IF;
    IF to_regclass('{{schema}}.record_seals') IS NOT NULL
        OR to_regclass('{{schema}}.record_seal_pages') IS NOT NULL
        OR to_regtype('{{schema}}.record_seals') IS NOT NULL
        OR to_regtype('{{schema}}.record_seal_pages') IS NOT NULL
        OR to_regprocedure('{{schema}}.record_seal_page_count()') IS NOT NULL
        OR to_regprocedure('{{schema}}.record_seal_page_immutable()') IS NOT NULL THEN
        RAISE EXCEPTION 'records extension 2 objects already exist under marker 1';
    END IF;

    CREATE TABLE {{schema}}.record_seals (
        conversation_id text NOT NULL, turn integer NOT NULL, draft text NOT NULL,
        created_at timestamptz NOT NULL, actor text, records integer NOT NULL CHECK (records > 0),
        bytes bigint NOT NULL CHECK (bytes >= 0), pages_seen integer NOT NULL DEFAULT 0,
        PRIMARY KEY (conversation_id, turn), UNIQUE (conversation_id, draft),
        UNIQUE (conversation_id, turn, records),
        FOREIGN KEY (conversation_id, turn) REFERENCES {{schema}}.turns(conversation_id, turn) ON DELETE CASCADE
    );
    CREATE TABLE {{schema}}.record_seal_pages (
        conversation_id text NOT NULL, turn integer NOT NULL, records integer NOT NULL,
        page integer NOT NULL CHECK (page >= 0 AND page <= (records - 1) / 32),
        digests bytea NOT NULL CHECK (octet_length(digests) = 32 * least(32, records - page * 32)),
        PRIMARY KEY (conversation_id, turn, page),
        FOREIGN KEY (conversation_id, turn, records)
            REFERENCES {{schema}}.record_seals(conversation_id, turn, records) ON DELETE CASCADE
    );

    -- Updating one header per affected turn keeps partial seal retries bounded.
    CREATE FUNCTION {{schema}}.record_seal_page_count() RETURNS trigger LANGUAGE plpgsql AS $fn$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            UPDATE {{schema}}.record_seals h SET pages_seen = h.pages_seen + d.n
            FROM (SELECT conversation_id, turn, count(*) AS n FROM added GROUP BY 1, 2) d
            WHERE (h.conversation_id, h.turn) = (d.conversation_id, d.turn);
        ELSE
            UPDATE {{schema}}.record_seals h SET pages_seen = h.pages_seen - d.n
            FROM (SELECT conversation_id, turn, count(*) AS n FROM removed GROUP BY 1, 2) d
            WHERE (h.conversation_id, h.turn) = (d.conversation_id, d.turn);
        END IF;
        RETURN NULL;
    END; $fn$;
    CREATE TRIGGER record_seal_pages_insert AFTER INSERT ON {{schema}}.record_seal_pages
        REFERENCING NEW TABLE AS added FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.record_seal_page_count();
    CREATE TRIGGER record_seal_pages_delete AFTER DELETE ON {{schema}}.record_seal_pages
        REFERENCING OLD TABLE AS removed FOR EACH STATEMENT EXECUTE FUNCTION {{schema}}.record_seal_page_count();

    CREATE FUNCTION {{schema}}.record_seal_page_immutable() RETURNS trigger LANGUAGE plpgsql AS $fn$
    BEGIN
        IF (OLD.conversation_id, OLD.turn, OLD.records, OLD.page) IS DISTINCT FROM
           (NEW.conversation_id, NEW.turn, NEW.records, NEW.page) THEN
            RAISE EXCEPTION 'receipt page identity is immutable';
        END IF;
        RETURN NEW;
    END; $fn$;
    CREATE TRIGGER record_seal_pages_identity BEFORE UPDATE ON {{schema}}.record_seal_pages
        FOR EACH ROW EXECUTE FUNCTION {{schema}}.record_seal_page_immutable();
    REVOKE EXECUTE ON FUNCTION {{schema}}.record_seal_page_count(), {{schema}}.record_seal_page_immutable() FROM PUBLIC;

    UPDATE {{schema}}.neosian_records SET version = 2 WHERE singleton AND version = 1;
    IF NOT FOUND OR (SELECT array_agg(CASE WHEN singleton THEN version END) FROM {{schema}}.neosian_records)
        IS DISTINCT FROM ARRAY[2] THEN
        RAISE EXCEPTION 'records extension 2 marker update failed';
    END IF;
END; $upgrade$;
COMMIT;
