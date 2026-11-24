-- Only relevance reads record bodies before the winners are chosen.
, json_build_object(
      'seq', r.seq, 'message', r.message, 'role', r.role, 'kind', r.kind,
      'content', r.content, 'block', r.block, 'call', r.call,
      'call_id', r.call_id, 'name', r.name, 'chunk', r.chunk,
      'size', r.size, 'text', r.text
  ) AS body, r.digest, r.neosian_format
