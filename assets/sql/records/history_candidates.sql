-- Share the selection and resume position without materializing turn text.
WITH scope AS NOT MATERIALIZED (
    SELECT t.conversation_id, t.turn, t.created_at, t.neosian_format, t.search_text, t.redacted
    FROM {{schema}}.turns t
    WHERE ($2::text[] IS NULL OR t.conversation_id = ANY($2::text[]))
      AND ($3::text IS NULL OR t.created_at >= $3::text::timestamptz)
      AND ($4::text IS NULL OR t.created_at < $4::text::timestamptz)
      AND ($5::text IS NULL OR (t.created_at, t.conversation_id COLLATE "C", t.turn)
             < ($5::text::timestamptz, $6::text COLLATE "C", $7::bigint))
), searchable AS NOT MATERIALIZED (
    SELECT conversation_id, owner, part, text, name FROM {{schema}}.turn_records
    WHERE kind IN ('prompt', 'reply', 'system', 'tool_result', 'tool_call')
)
SELECT * FROM (
    SELECT t.conversation_id, t.turn, t.created_at, t.neosian_format, {{legacy_text}} AS search_text
    FROM scope t
    WHERE t.neosian_format <> 2 AND t.search_text ILIKE {{legacy_match}}($1::text[])
    UNION ALL
    SELECT t.conversation_id, t.turn, t.created_at, t.neosian_format, NULL::text AS search_text
    FROM scope t
    JOIN {{schema}}.record_turns m ON (m.conversation_id, m.turn) = (t.conversation_id, t.turn)
    WHERE t.neosian_format = 2 AND NOT t.redacted AND ({{native_match}})
) AS candidates
{{ordering}}
LIMIT $8::bigint
