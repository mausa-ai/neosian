-- Count the exact pending owners and their physical dependants on the same
-- snapshot as deletion. The caller rolls back if a trigger suppresses a row.
WITH pending AS MATERIALIZED (
    SELECT owner FROM {{schema}}.record_drafts WHERE conversation_id = $1 AND state = 'open'
), dropped AS (
    DELETE FROM {{schema}}.record_drafts d USING pending p
    WHERE d.conversation_id = $1 AND d.owner = p.owner AND d.state = 'open'
    RETURNING d.owner, d.kind
), items AS (
    DELETE FROM {{schema}}.record_staging s USING dropped d
    WHERE s.conversation_id = $1 AND s.owner = d.owner RETURNING 1
), records AS (
    DELETE FROM {{schema}}.turn_records r USING dropped d
    WHERE r.conversation_id = $1 AND r.owner = d.owner RETURNING 1
)
SELECT count(*) FILTER (WHERE kind = 'write') AS drafts,
       count(*) FILTER (WHERE kind = 'restore') AS restores,
       (SELECT count(*) FROM {{schema}}.record_staging s JOIN pending USING (owner)
        WHERE s.conversation_id = $1) = (SELECT count(*) FROM items)
       AND (SELECT count(*) FROM {{schema}}.turn_records r JOIN pending USING (owner)
            WHERE r.conversation_id = $1) = (SELECT count(*) FROM records) AS complete
FROM dropped
