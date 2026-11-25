-- JSONB equality ignores number scale; its stored rendering preserves it.
SELECT messages = $3::jsonb AS equal_value,
       messages::text COLLATE "C" = ($3::jsonb)::text AS equal_spelling,
       search_text COLLATE "C" IS NOT DISTINCT FROM $4::text AS equal_search
FROM {{schema}}.turns WHERE conversation_id = $1 AND turn = $2
