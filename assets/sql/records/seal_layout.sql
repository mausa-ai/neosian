-- A zero-column probe also works with a one-byte response budget. The row
-- count is 0 when absent, 1 when incomplete and 2 for the complete layout.
WITH members AS (
    SELECT to_regclass('{{schema}}.record_seals') AS heads,
           to_regclass('{{schema}}.record_seal_pages') AS pages,
           to_regclass('{{schema}}.turns') AS turns,
           to_regprocedure('{{schema}}.record_seal_page_count()') AS counter,
           to_regprocedure('{{schema}}.record_seal_page_immutable()') AS guard
), columns AS (
    SELECT fields.* FROM members CROSS JOIN LATERAL (VALUES
        (heads,'conversation_id','text',true), (heads,'turn','int4',true),
        (heads,'draft','text',true), (heads,'created_at','timestamptz',true),
        (heads,'actor','text',false), (heads,'records','int4',true),
        (heads,'bytes','int8',true), (heads,'pages_seen','int4',true),
        (pages,'conversation_id','text',true), (pages,'turn','int4',true),
        (pages,'records','int4',true), (pages,'page','int4',true), (pages,'digests','bytea',true)
    ) AS fields(rel,name,typ,not_null)
), required_keys AS (
    SELECT keys.* FROM members CROSS JOIN LATERAL (VALUES
        (heads, 'p', NULL::regclass, ARRAY['conversation_id','turn']),
        (heads, 'u', NULL::regclass, ARRAY['conversation_id','draft']),
        (heads, 'u', NULL::regclass, ARRAY['conversation_id','turn','records']),
        (pages, 'p', NULL::regclass, ARRAY['conversation_id','turn','page']),
        (heads, 'f', turns, ARRAY['conversation_id','turn']),
        (pages, 'f', heads, ARRAY['conversation_id','turn','records'])
    ) AS keys(rel,kind,parent,fields)
), actual_keys AS (
    -- Resolve the constraint columns on each relation in ordinal order.
    -- Constraint names and the session's search_path carry no identity.
    SELECT c.conrelid, c.contype, c.confrelid,
           ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY AS k(num, ord)
                 JOIN pg_catalog.pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.num
                 ORDER BY k.ord) AS fields,
           ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY AS k(num, ord)
                 JOIN pg_catalog.pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num
                 ORDER BY k.ord) AS parent_fields
    FROM members JOIN pg_catalog.pg_constraint c ON c.conrelid IN (heads, pages)
    JOIN pg_catalog.pg_index i ON i.indexrelid = c.conindid
    JOIN pg_catalog.pg_class idx ON idx.oid = i.indexrelid
    JOIN pg_catalog.pg_am am ON am.oid = idx.relam
    WHERE c.contype IN ('p','u','f') AND c.convalidated AND NOT c.condeferrable
      AND i.indisunique AND i.indisvalid AND i.indisready AND i.indislive AND i.indimmediate
      AND am.amname = 'btree' AND i.indpred IS NULL
      AND (c.contype <> 'f' OR (c.confdeltype = 'c' AND c.confupdtype = 'a' AND c.confmatchtype = 's'))
), check_constraints AS MATERIALIZED (
    -- Bound relation-opening deparsers to this installation before evaluation.
    SELECT c.conrelid, c.oid FROM members
    JOIN pg_catalog.pg_constraint c ON c.conrelid IN (heads, pages)
    WHERE c.contype = 'c' AND c.convalidated
), page_defaults AS MATERIALIZED (
    SELECT d.adbin, d.adrelid FROM members
    JOIN pg_catalog.pg_attribute a ON a.attrelid = heads AND a.attname = 'pages_seen'
    JOIN pg_catalog.pg_attrdef d ON (a.attrelid, a.attnum) = (d.adrelid, d.adnum)
), checks AS (
    SELECT required.* FROM members CROSS JOIN LATERAL (VALUES
        (heads, 'CHECK ((records > 0))'), (heads, 'CHECK ((bytes >= 0))'),
        (pages, 'CHECK (((page >= 0) AND (page <= ((records - 1) / 32))))'),
        (pages, 'CHECK ((octet_length(digests) = (32 * LEAST(32, (records - (page * 32))))))')
    ) AS required(rel,definition)
), triggers AS (
    SELECT required.* FROM members CROSS JOIN LATERAL (VALUES
        ('record_seal_pages_insert', 4, counter, NULL, 'added'),
        ('record_seal_pages_delete', 8, counter, 'removed', NULL),
        ('record_seal_pages_identity', 19, guard, NULL, NULL)
    ) AS required(name,event,func,old_table,new_table)
)
SELECT FROM members WHERE heads IS NOT NULL OR pages IS NOT NULL OR counter IS NOT NULL OR guard IS NOT NULL
UNION ALL
SELECT FROM members
WHERE (SELECT count(*) FROM pg_catalog.pg_class WHERE oid IN (heads,pages) AND relkind IN ('r','p')) = 2
  AND NOT EXISTS (SELECT FROM columns e WHERE NOT EXISTS (
      SELECT FROM pg_catalog.pg_attribute a WHERE a.attrelid = e.rel AND a.attname = e.name
        AND a.atttypid = ('pg_catalog.' || e.typ)::regtype AND a.attnotnull = e.not_null
        AND a.attnum > 0 AND NOT a.attisdropped
  ))
  AND NOT EXISTS (SELECT FROM required_keys e WHERE NOT EXISTS (
      SELECT FROM actual_keys a WHERE a.conrelid = e.rel AND a.contype = e.kind::"char" AND a.fields = e.fields
        AND (e.kind <> 'f' OR (a.confrelid = e.parent AND a.parent_fields = e.fields))
  ))
  AND NOT EXISTS (SELECT FROM checks e WHERE NOT EXISTS (
      SELECT FROM check_constraints c WHERE c.conrelid = e.rel
        AND pg_catalog.pg_get_constraintdef(c.oid) = e.definition
  ))
  AND EXISTS (SELECT FROM page_defaults WHERE pg_catalog.pg_get_expr(adbin, adrelid) = '0')
  AND NOT EXISTS (SELECT FROM triggers e WHERE NOT EXISTS (
      SELECT FROM pg_catalog.pg_trigger t WHERE t.tgrelid = pages AND t.tgname = e.name
        AND t.tgfoid = e.func AND t.tgtype = e.event AND NOT t.tgisinternal AND t.tgenabled IN ('O','A')
        AND t.tgqual IS NULL AND t.tgnargs = 0 AND t.tgattr = ''::int2vector
        AND t.tgoldtable IS NOT DISTINCT FROM e.old_table AND t.tgnewtable IS NOT DISTINCT FROM e.new_table
  ))
