-- Counts were accumulated from the inserted physical records at admission.
-- Resolve references against the staged header keys, including skeletons.
WITH headers AS (
    SELECT ord, body FROM {{schema}}.record_staging
    WHERE conversation_id = $1 AND owner = $2 AND item = 'turn'
), refs AS (
    SELECT (body->>'turn')::bigint AS turn FROM {{schema}}.record_staging
    WHERE conversation_id = $1 AND owner = $2 AND item = 'projection'
    UNION ALL
    SELECT n.turn::bigint FROM {{schema}}.record_staging AS s
    CROSS JOIN LATERAL jsonb_array_elements_text(s.body->'turns') AS n(turn)
    WHERE s.conversation_id = $1 AND s.owner = $2 AND s.item = 'redaction'
)
SELECT EXISTS (SELECT 1 FROM {{schema}}.turns WHERE conversation_id = $1)
       OR EXISTS (SELECT 1 FROM {{schema}}.projections WHERE conversation_id = $1) AS occupied,
       NOT EXISTS (
           SELECT 1 FROM headers
           WHERE body->'turn' IS DISTINCT FROM to_jsonb(ord)
              OR jsonb_typeof(body->'records') IS DISTINCT FROM 'number'
              OR jsonb_typeof(body->'bytes') IS DISTINCT FROM 'number'
              OR body->'records' IS DISTINCT FROM body->'received_records'
              OR body->'bytes' IS DISTINCT FROM body->'received_bytes'
       )
       AND NOT EXISTS (SELECT turn FROM refs EXCEPT SELECT ord FROM headers)
       AND (SELECT coalesce(max(ord), 0) FROM headers) = $3::bigint
       AND coalesce((SELECT body->'records' FROM headers WHERE ord = $3), '0'::jsonb)
           = to_jsonb($4::bigint)
       AND (SELECT count(*) FROM {{schema}}.record_staging
            WHERE conversation_id = $1 AND owner = $2 AND item = 'projection') = $5::bigint
       AND (SELECT count(*) FROM {{schema}}.record_staging
            WHERE conversation_id = $1 AND owner = $2 AND item = 'redaction') = $6::bigint
       AS complete
