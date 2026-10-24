SELECT to_regclass('{{schema}}.neosian_changes') IS NOT NULL AS marker,
       to_regclass('{{schema}}.memory_change_streams') IS NOT NULL AS streams,
       to_regclass('{{schema}}.memory_changes') IS NOT NULL AS events
