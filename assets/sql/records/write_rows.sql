INSERT INTO {{schema}}.turn_records
    (conversation_id, owner, part, seq, message, role, kind, content, block, call,
     call_id, name, chunk, size, last, text, digest)
SELECT $1, $2, $4::integer, r.seq, r.message, r.role, r.kind, r.content, r.block, r.call,
       r.call_id, r.name, r.chunk, r.size, r.last, r.text, decode(r.digest, 'hex')
FROM jsonb_to_recordset($3::text::jsonb) AS r(
    seq integer, message integer, role text, kind text, content text, block integer,
    call integer, call_id text, name text, chunk integer, size bigint,
    last boolean, text text, digest text)
