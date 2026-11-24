SELECT count(*) AS present,
       count(*) FILTER (WHERE relkind IN ('r', 'p')) AS tables
FROM pg_catalog.pg_class
WHERE oid IN (to_regclass('{{schema}}.neosian_records'),
              to_regclass('{{schema}}.record_drafts'),
              to_regclass('{{schema}}.turn_records'),
              to_regclass('{{schema}}.record_turns'),
              to_regclass('{{schema}}.record_staging'))
