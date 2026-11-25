-- Publish the already validated metadata. The physical record rows remain
-- where they were admitted, and are now visible through their mapping.
WITH staged AS (
    DELETE FROM {{schema}}.record_staging
    WHERE conversation_id = $1 AND owner = $2
    RETURNING ord, item, body
), headers AS (
    SELECT ord::integer AS turn, (body->>'created_at')::timestamptz AS stamp,
           body->>'actor' AS actor, (body->>'redacted')::boolean AS redacted,
           (body->>'records')::integer AS records, (body->>'bytes')::bigint AS bytes
    FROM staged WHERE item = 'turn'
), parent AS (
    INSERT INTO {{schema}}.conversations (conversation_id, created_at)
    VALUES ($1, coalesce((SELECT stamp FROM headers ORDER BY turn LIMIT 1), clock_timestamp()))
    ON CONFLICT (conversation_id) DO NOTHING RETURNING 1
), rows AS (
    INSERT INTO {{schema}}.turns
        (conversation_id, turn, messages, created_at, actor, neosian_format, search_text, redacted)
    SELECT $1, turn, '[]'::jsonb, stamp, actor, CASE WHEN redacted THEN 1 ELSE 2 END,
           CASE WHEN redacted THEN '' END, redacted FROM headers
), mapped AS (
    INSERT INTO {{schema}}.record_turns
        (conversation_id, turn, owner, part, draft, records, bytes, created_at)
    SELECT $1, turn, $2, turn, $3, records, bytes, stamp FROM headers WHERE NOT redacted
), projections AS (
    INSERT INTO {{schema}}.projections (conversation_id, turn, kind, text, span, neosian_format)
    SELECT $1, (body->>'turn')::integer, body->>'kind', body->>'text', (body->>'span')::integer, 1
    FROM staged WHERE item = 'projection' ORDER BY ord DESC
), trail AS (
    INSERT INTO {{schema}}.turn_redactions (conversation_id, turns, actor, created_at)
    SELECT $1, ARRAY(SELECT n.turn::integer FROM jsonb_array_elements_text(body->'turns')
                     WITH ORDINALITY AS n(turn, pos) ORDER BY n.pos),
           body->>'actor', (body->>'created_at')::timestamptz
    FROM staged WHERE item = 'redaction' ORDER BY ord DESC
), marker AS (
    UPDATE {{schema}}.record_drafts
    SET state = 'committed', position = $4::jsonb, bytes = $5::bigint, touched_at = clock_timestamp()
    WHERE conversation_id = $1 AND owner = $2 AND kind = 'restore' AND state = 'open'
    RETURNING 1
)
SELECT EXISTS (SELECT FROM parent) AS created, EXISTS (SELECT FROM marker) AS marked
