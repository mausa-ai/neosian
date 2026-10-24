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
Its full/base profiles state which optional capabilities were verified; the
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
