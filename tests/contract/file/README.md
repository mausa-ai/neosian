# FileStore physical contracts

These cases plant literal format-1 files and call the Rust server through raw
HTTP. Run them with the rest of the independent tier using `make test-contract`,
or select just this directory:

```sh
uv run pytest --confcutdir=tests/contract tests/contract/file -m contract -q
```

Every case owns a temporary home. The runner imports no Python product code.
Its checks cover:

- Malformed envelopes, version journals, turns, projections and erasure
  trails, including future formats, invalid field types and naive timestamps.
  A small read window does not conceal an unreadable row.
- Byte preservation when an unreadable document or version journal blocks
  a memory write/delete/rename, or when an unreadable turn log blocks append
  or erasure. Invalid destination history prevents a rename from changing
  its source. These checks do not promise cross-file transactions.
- Version allocation after a hand-planted document with no journal, or a
  journal ahead of its document; turn allocation from the last row's number.
- Unknown turn-row keys, escaped search terms and the file search prefilter's
  treatment of malformed candidate versus non-candidate lines.
- Private file/directory modes after writes, erasure and restore; absence of
  temporary-file leftovers; reads and empty restores creating no state.
- Memory document/journal symlink containment, conversation read/append
  containment, foreign-name filtering and history-only scope enumeration.
- Reference FileStore cursor decoding, including signed history keys.
  These tests deliberately know that substrate's cursor representation;
  the generic HTTP conformance kit treats service cursors as opaque.

POSIX permission checks skip on Windows; symlink checks skip there only if
the runner cannot create a symlink. Missing binaries fail. This corpus does
not establish crash recovery, cross-file rollback or complete containment
coverage for every operator. Postgres corruption/schema-generation cases,
deeper backend races and the remaining transport/client/operator corpus
retain their existing suites during the S0 transition.
