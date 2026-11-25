-- When adding omitted defaults changes the JSONB value, check independently
-- whether decoding the source also rounded a high-precision number.
SELECT messages = $3::jsonb AS preserved FROM {{schema}}.turns
WHERE conversation_id = $1 AND turn = $2
