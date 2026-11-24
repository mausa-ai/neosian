-- Write records only on first admission. Increase the touched headers'
-- physical counts from the inserted rows, never by scanning earlier parts.
WITH wanted AS (
    SELECT * FROM jsonb_to_recordset($3::text::jsonb) AS w(
        turn integer, seq integer, message integer, role text, kind text, content text,
        block integer, call integer, call_id text, name text, chunk integer, size bigint,
        text text, last boolean, digest text, repeated boolean)
), added AS (
    INSERT INTO {{schema}}.turn_records
        (conversation_id, owner, part, seq, message, role, kind, content, block, call,
         call_id, name, chunk, size, text, last, digest)
    SELECT $1, $2, turn, seq, message, role, kind, content, block, call, call_id,
           name, chunk, size, text, last, decode(digest, 'hex')
    FROM wanted WHERE NOT repeated
    RETURNING part, octet_length(text) AS text_bytes
), growth AS (
    SELECT part, count(*) AS records, sum(text_bytes) AS bytes FROM added GROUP BY part
), counted AS (
    UPDATE {{schema}}.record_staging s
       SET body = s.body || jsonb_build_object(
           'received_records', (s.body->>'received_records')::bigint + g.records,
           'received_bytes',   (s.body->>'received_bytes')::bigint + g.bytes)
      FROM growth g
     WHERE (s.conversation_id, s.owner, s.ord) = ($1, $2, g.part)
       AND s.item = 'turn' AND jsonb_typeof(s.body->'received_records') = 'number'
       AND jsonb_typeof(s.body->'received_bytes') = 'number'
    RETURNING s.ord
)
SELECT (SELECT count(*) FROM growth) = (SELECT count(*) FROM counted) AS counted,
       NOT EXISTS (
           SELECT 1 FROM wanted w
           LEFT JOIN LATERAL (
               SELECT * FROM {{schema}}.turn_records
               WHERE (conversation_id, owner, part, seq) = ($1, $2, w.turn, w.seq) LIMIT 1
           ) r ON true
           WHERE w.repeated AND (
               r.neosian_format IS DISTINCT FROM 1 OR r.digest IS DISTINCT FROM decode(w.digest, 'hex')
               OR (r.message, r.role, r.kind, r.content, r.block, r.call, r.call_id, r.name, r.chunk, r.size, r.text)
                  IS DISTINCT FROM
                  (w.message, w.role, w.kind, w.content, w.block, w.call, w.call_id, w.name, w.chunk, w.size, w.text))
       ) AS matches
