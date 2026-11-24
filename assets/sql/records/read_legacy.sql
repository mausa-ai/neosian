-- An old turn can exceed the response budget even when its projected records
-- fit. Encode its existing JSON once and transfer it in independently sized
-- rows. Byte positions preserve Unicode across chunk boundaries.
WITH document AS MATERIALIZED (
    SELECT convert_to(messages::text, 'UTF8') AS body
    FROM {{schema}}.turns
    WHERE conversation_id = $1 AND turn = $2
)
SELECT pos, replace(encode(substring(body FROM pos + 1 FOR $3), 'base64'), chr(10), '') AS chunk
FROM document
CROSS JOIN LATERAL generate_series(0, octet_length(body) - 1, $3::integer) AS positions(pos)
