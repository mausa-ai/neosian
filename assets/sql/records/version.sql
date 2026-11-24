-- A damaged marker still returns a named configuration refusal. Read its
-- fields as JSON so a missing column cannot fail during statement preparation.
SELECT to_jsonb(marker) AS marker FROM {{schema}}.neosian_records AS marker LIMIT 2
