-- Inspect at most two markers. A missing/invalid field must produce the
-- named configuration refusal even when the marker table is malformed.
-- Return no fields: counts 0, 1, 2 and 3 mean invalid, v1, v2 and unsupported.
WITH sample AS (
    SELECT to_jsonb(m) AS marker FROM {{schema}}.neosian_records m LIMIT 2
), version AS (
    SELECT marker -> 'version' AS v FROM sample
    WHERE (SELECT count(*) FROM sample) = 1 AND marker -> 'singleton' = 'true'::jsonb
), integer_version AS (
    SELECT v FROM version WHERE jsonb_typeof(v) = 'number'
      AND v::text ~ '^-?[0-9]+$'
      AND v BETWEEN '-9223372036854775808'::jsonb AND '9223372036854775807'::jsonb
)
SELECT FROM integer_version
CROSS JOIN LATERAL generate_series(1, CASE v WHEN '1'::jsonb THEN 1
                                             WHEN '2'::jsonb THEN 2 ELSE 3 END)
