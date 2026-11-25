-- A half-open sequence range addresses indexed pages. Even at the signed
-- int4 limit, page arithmetic fits bigint and the slice fits one KiB.
SELECT p.page,
       substring(p.digests
           FROM 1 + 32 * greatest($3::bigint - p.page::bigint * 32, 0)::integer
           FOR 32 * (least($4::bigint, (p.page::bigint + 1) * 32)
                     - greatest($3::bigint, p.page::bigint * 32))::integer) AS requested
FROM {{schema}}.record_seal_pages p
WHERE p.conversation_id = $1 AND p.turn = $2
  AND p.page >= ($3::bigint / 32)::integer AND p.page <= (($4::bigint - 1) / 32)::integer
ORDER BY p.page
