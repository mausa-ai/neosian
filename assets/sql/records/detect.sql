-- Return no data fields. The command count says none (0), incomplete (1)
-- or all five relations of a supported table kind (2).
WITH members AS (
    SELECT count(*) AS found, count(*) FILTER (WHERE relkind IN ('r', 'p')) AS tables
    FROM pg_catalog.pg_class
    WHERE oid IN (to_regclass('{{schema}}.neosian_records'),
                  to_regclass('{{schema}}.record_drafts'),
                  to_regclass('{{schema}}.turn_records'),
                  to_regclass('{{schema}}.record_turns'),
                  to_regclass('{{schema}}.record_staging'))
)
SELECT FROM members WHERE found > 0
UNION ALL
SELECT FROM members WHERE tables = 5
