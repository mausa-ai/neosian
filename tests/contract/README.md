# Independent core contracts

Run `cargo build --locked`, then `make test-contract`. Set `NEOSIAN_BINARY`
to test another executable. A missing binary fails; this tier never silently
skips it. CI runs the release binary on Linux.

The runner uses pytest, JSON Schema validation, a subprocess and raw HTTP/JSON-RPC.
It does not import `neosian`, its codecs, fakes, stores or conformance classes.
Python is development tooling only; no product module is installed or imported.
Every process gets a temporary home and isolated client configuration.

`fixtures/v1/records.json` and `fixtures/v1/home/` are independently authored
format-1 records and their physical FileStore representation. They are literal
inputs and expectations, never snapshots regenerated from the implementation
under test. The fixtures cover Unicode, nested metadata, full message content
(media, tool calls, reasoning and opaque provider fields), historical versions,
erasures, projection spans and a mailbox record. The physical memory erasure
trail uses `ts`; the wire uses `created_at`. Neither difference is normalised
away in the fixture.

The tests cover all 21 base storage POST operations, optional memory search and change feeds,
the handshake, transport and
allowance refusals, paging and occupied restores, the six memory commands,
all ten mailbox operations, and a stdio MCP session. Successful storage requests
and responses validate against `assets/contracts/wire-v6.openapi.json`;
stored rows validate against the adjacent `state-v1.schema.json` definitions.
Generated timestamps, IDs and claim tokens are checked by shape and lifecycle;
the expected content, versions, actors and failure codes stay literal.

The [storage suite](storage/README.md) also runs against two file-backed or
Postgres server processes, or an explicitly selected external test service.
Its full/base profiles state which optional capabilities were verified, and
its records profile also requires turn records version 1; the
container gate runs the full profile on both backends. This adds independent
coverage for storage behavior and concurrent writes. The
[FileStore corpus](file/README.md) plants malformed files, checks refusal
without data replacement, private modes, basic containment, numbering and
reference cursor decoding. `postgres/` plants malformed/future rows and checks
schema generation. `replay/` freezes 261 Python 1.13.0 output/file-delta cases
before the product cut, including operators, hooks and HTTP MCP sessions.
Verified HTTPS, migration, six-client setup, maintenance and the current
embedded manifest have additional independent cases. Node harnesses under
`tests/clients/` exercise the generated Pi and OpenCode adapters.

Memory-search cases pin Unicode/metadata matching, current-row lifecycle,
opaque cursor forwarding, profile references, raw grant denials and malformed
backend replies. The optional storage cases run unchanged on file, Postgres
and external HTTP services; absence of the declared capability is an explicit
skip, including under the full base-storage profile.

Turn-records cases in `records/` enable a FileStore by writing its marker and
send literal `conversation/write_records` bodies: every field and bound, each
conflict reason with its details, both `413` refusals, and the logical bytes
of each answer computed in Python from the published size rule.
`records/test_read*.py` do the same for `conversation/read_records`: every
field and refusal, the order of the checks before the store is read, each
view, heads, a piece fetched whole, pages checked for their bound and for
being full, continuations, the key order of the raw answer and the answer's
size bound measured on its bytes. The design's example is read from a home
planted by hand. `records/test_search*.py` cover
`conversation/search_records`: every field and refusal, both orders with
literal scores from a BM25 reference that shares no code with the tree, hit
key order and the answer's bound on raw bytes, the design's example, what
never matches, and that a client outside its allowance or grants is refused
while the store's lock is held by the test. `records/test_sweep.py` covers
`store/sweep_records` over stale drafts and restores planted with old
times: preview equal to removal, `limit`, cursors and their forgeries,
literal `freed_bytes` and the operator-only rule.
`records/test_restore*.py` cover `store/restore_records`: the design's
example with its literal bytes and a unit digest recomputed by a Python
reference that shares no code with the tree, the probe and its position,
repeats under other part boundaries, each conflict reason with its
details, both `target_occupied` cases, the discard, a staged restore
absent from every read, search and listing and swept when stale, strict
decoding of the body and of every item, both `413` refusals, the answer's
key order and bound on raw bytes, and a restored conversation read equal
to its source.
`postgres/test_records_wire.py` repeats the write and read examples, the
reasons, the views and the pages on records extension 1,
`postgres/test_records_wire_search.py` the search example, both orders,
what never matches, the candidate bound and a sweep, and
`postgres/test_records_wire_restore.py` the restore example, the two
`target_occupied` cases, a staged restore no read sees and a stale one
swept.
`records/test_legacy_bytes.py` sends one script of requests over every
legacy route to two servers, a plain FileStore and one that keeps records
and holds only whole turns, and compares the answers as bytes (a time the
server stamped, a change stream's name and a continuation cut from stamped
turns are replaced on both sides first); the handshakes differ by the one
`turn_records` key, last in the bytes. It then writes a 70,000-byte turn
through `write_records` and reads the long-turn refusal and `omitted` from
the legacy routes. `postgres/test_legacy_bytes.py` sends the same script to
two Postgres schemas, one with records extension 1. `test_http.py` keeps the literal handshake of a plain
store and adds the literal handshake of a store that keeps records, and
`test_storage_profiles.py` runs the storage suite in its external HTTP mode
against a server it did not start: the records profile fails on a service
that does not declare `turn_records` and passes on one that does, and a
service that answers the five routes `404` passes the full profile.
`records/test_write_bounds.py` pins the handshake's refusal on a damaged
records marker.

Governance cases independently hash review subjects and cover CLI/MCP proposals,
profile retrieval floors, skill graphs, references, source-preserving recovery,
revocation and file/Postgres erasure. The optional HTTP filter cases also run on
custom services. Raw read authority remains separate from review selection.

The release's `neosian-contracts.tar.gz` carries this tier and its locked
test dependencies. Extract into a new directory, run `make install`, then
`make test-storage-contract backend=http` against a disposable service. No
binary or Rust checkout is needed for HTTP mode. Local file/Postgres probes
require an installed binary selected by absolute `NEOSIAN_BINARY`.

Change an expectation only with an explained contract decision. Preserve old
readable fixtures when a new format is introduced; add a new version directory
and explicit conversion/refusal cases. Do not bless a regression by recording
the current program's output as the new expected answer.

Optional change-feed v1 runs shared replay, mutation, pagination, concurrency and
retention cases when advertised. File recovery probes kill a real writer after
its journal appears and validate recovered state/events in a new process.
Postgres probes force event/retention failures and assert transaction rollback;
legacy schema 4 and unsupported extension installations have separate checks.
The frozen v1 replay projection excludes only added discovery and `.changes/`/
`.transactions/` internals; its original expected files remain untouched.

Exchange contracts cover current snapshots and coalesced deltas, explicit scope
authority, literal metadata/cursors, replay/order/conflicts, lost receipts, source
gaps, multi-page intervals and physical erasure. Real receiver processes are
killed during snapshot/delta/erasure apply; a new process must recover both data
and checkpoint. Direct and HTTP Postgres sources use disposable test schemas.
These tests do not claim bidirectional or remote receiver synchronization.
