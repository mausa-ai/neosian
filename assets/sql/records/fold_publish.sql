-- Only this mapped part becomes unnecessary. A committed restore may own
-- many such parts, and its independent replay receipt remains untouched.
WITH boundaries AS (
    SELECT last, lead(chunk, 1, 0) OVER (ORDER BY seq) = 0 AS ends_piece
    FROM {{schema}}.turn_records WHERE conversation_id = $1 AND owner = $3 AND part = $4
), published AS (
    UPDATE {{schema}}.turns SET messages = $5, search_text = $6, neosian_format = 1
    WHERE conversation_id = $1 AND turn = $2 AND neosian_format = 2
    RETURNING 1
), unlinked AS (
    DELETE FROM {{schema}}.record_turns
    WHERE conversation_id = $1 AND turn = $2 AND owner = $3 AND part = $4
    RETURNING 1
), removed AS (
    DELETE FROM {{schema}}.turn_records
    WHERE conversation_id = $1 AND owner = $3 AND part = $4 RETURNING 1
)
SELECT (SELECT count(*) FROM published) AS published, (SELECT count(*) FROM unlinked) AS unlinked,
       (SELECT count(*) FROM removed) AS removed,
       NOT EXISTS (SELECT FROM boundaries WHERE last <> ends_piece) AS valid
