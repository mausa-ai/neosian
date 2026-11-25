-- The caller holds the records conversation lock. Lock selected headers too
-- so an online migration and erasure agree on which representation to erase.
WITH matched AS MATERIALIZED (
    SELECT turn, neosian_format, created_at, redacted,
           CASE WHEN neosian_format = 2 THEN messages = '[]'::jsonb END AS empty_header
    FROM {{schema}}.turns
    WHERE conversation_id = %(conversation_id)s
      AND (%(through)s::integer IS NULL OR turn <= %(through)s)
      AND (%(all)s::boolean OR turn = ANY(%(turns)s::integer[]))
    ORDER BY turn FOR UPDATE
), mapped AS (
    SELECT t.turn, m.owner, m.part,
           (m.owner > 0 AND m.part IN (0, t.turn) AND t.turn > 0
            AND t.empty_header AND NOT t.redacted AND t.created_at = m.created_at
            AND m.bytes >= 0 AND m.bytes = p.bytes AND m.records > 0 AND p.n = m.records
            AND p.first_seq = 0 AND p.last_seq = m.records::bigint - 1) AS valid,
           p.bad_format
    FROM matched t
    LEFT JOIN {{schema}}.record_turns m
      ON m.conversation_id = %(conversation_id)s AND m.turn = t.turn
    CROSS JOIN LATERAL (
        SELECT count(*) AS n, min(seq) AS first_seq, max(seq) AS last_seq,
               sum(size) FILTER (WHERE chunk = 0) AS bytes,
               min(neosian_format) FILTER (WHERE neosian_format <> 1) AS bad_format
        FROM {{schema}}.turn_records r
        WHERE r.conversation_id = %(conversation_id)s AND (r.owner, r.part) = (m.owner, m.part)
    ) p
    WHERE t.neosian_format = 2
), covered AS (
    SELECT p.id, p.neosian_format FROM {{schema}}.projections p
    WHERE p.conversation_id = %(conversation_id)s
      AND EXISTS (SELECT FROM matched t
                  WHERE t.turn BETWEEN p.turn::bigint - p.span + 1 AND p.turn)
), invalid AS (
    SELECT neosian_format FROM matched WHERE neosian_format NOT IN (1, 2)
    UNION ALL SELECT neosian_format FROM covered WHERE neosian_format <> 1
    UNION ALL SELECT bad_format FROM mapped WHERE bad_format IS NOT NULL
    UNION ALL SELECT neosian_format FROM {{schema}}.turn_records
              WHERE %(full)s::boolean AND conversation_id = %(conversation_id)s AND neosian_format <> 1
), gate AS (
    SELECT NOT EXISTS (SELECT FROM invalid)
       AND NOT EXISTS (SELECT FROM mapped WHERE valid IS NOT TRUE)
       AND NOT EXISTS (
           SELECT FROM matched t JOIN {{schema}}.record_turns m
             ON m.conversation_id = %(conversation_id)s AND m.turn = t.turn
           WHERE t.neosian_format = 1
       ) AS ok
), blanked AS (
    UPDATE {{schema}}.turns t
    SET messages = '[]'::jsonb, search_text = '', redacted = true, neosian_format = 1
    FROM matched WHERE t.conversation_id = %(conversation_id)s AND t.turn = matched.turn
      AND (SELECT ok FROM gate)
), projections AS (
    UPDATE {{schema}}.projections p SET text = '' FROM covered
    WHERE p.id = covered.id AND (SELECT ok FROM gate)
), links AS (
    DELETE FROM {{schema}}.record_turns m
    WHERE m.conversation_id = %(conversation_id)s AND (SELECT ok FROM gate)
      AND (%(full)s OR m.turn IN (SELECT turn FROM mapped))
), selected_records AS (
    DELETE FROM {{schema}}.turn_records r USING mapped
    WHERE NOT %(full)s AND r.conversation_id = %(conversation_id)s AND (SELECT ok FROM gate)
      AND (r.owner, r.part) = (mapped.owner, mapped.part)
), all_records AS (
    DELETE FROM {{schema}}.turn_records
    WHERE %(full)s AND conversation_id = %(conversation_id)s AND (SELECT ok FROM gate)
), staging AS (
    DELETE FROM {{schema}}.record_staging
    WHERE %(full)s AND conversation_id = %(conversation_id)s AND (SELECT ok FROM gate)
), drafts AS (
    DELETE FROM {{schema}}.record_drafts
    WHERE %(full)s AND conversation_id = %(conversation_id)s AND (SELECT ok FROM gate)
), trail AS (
    INSERT INTO {{schema}}.turn_redactions (conversation_id, turns, actor, created_at)
    SELECT %(conversation_id)s, array_agg(turn ORDER BY turn), %(actor)s, %(now)s
    FROM matched HAVING count(*) > 0 AND (SELECT ok FROM gate)
)
SELECT (SELECT count(*) FROM matched) AS matched, (SELECT ok FROM gate) AS format_ok,
       (SELECT neosian_format FROM invalid LIMIT 1) AS current_format
