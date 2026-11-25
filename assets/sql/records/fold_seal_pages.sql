-- Keep the exact record split that the writer sealed. Each page holds at
-- most 32 SHA-256 digests, and no record text enters the receipt.
INSERT INTO {{schema}}.record_seal_pages (conversation_id, turn, records, page, digests)
SELECT $1, $2, $4::integer, r.seq / 32, string_agg(r.digest, ''::bytea ORDER BY r.seq)
FROM {{schema}}.turn_records r
WHERE r.conversation_id = $1 AND r.owner = $3 AND r.part = 0
GROUP BY r.seq / 32
