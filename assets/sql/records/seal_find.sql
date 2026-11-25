-- A canonical re-migration may change the record boundaries. The folded
-- receipt retains the original header in either visible turn format.
SELECT h.turn, h.records, h.bytes, h.created_at, h.actor, t.neosian_format,
       NOT t.redacted AND t.created_at = h.created_at AND t.actor IS NOT DISTINCT FROM h.actor
           AND t.neosian_format IN (1, 2) AND h.turn > 0 AND h.records > 0 AND h.bytes >= 0
           AND h.pages_seen = (h.records - 1) / 32 + 1 AS valid
FROM {{schema}}.record_seals h LEFT JOIN {{schema}}.turns t USING (conversation_id, turn)
WHERE h.conversation_id = $1 AND h.draft = $2
