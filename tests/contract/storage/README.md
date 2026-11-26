# Storage HTTP conformance

This suite exercises storage wire 6 without importing `neosian` or using
`RemoteStore`. It sends raw HTTP requests, validates successful records against
the published schemas and checks independently written behavioral expectations.
It covers memory/version/erasure history, scope/path/ID validation, turns and
projections, lexical history search, paging, restore and concurrent writes.
When `memory_search: {version: 1}` is declared, it also tests current-memory
matching, Unicode 17.0.0, selected metadata, keyset paging and erasure. These
cases are optional under both base/full profiles and report explicit skips
when search is absent. Metadata fixture cases additionally require portability.

When `knowledge_policy: {version: 1}` is declared, governance cases independently
compute approval hashes and verify effective/approved filtering, invalid/future
records, policy-bound paging and current erasure. This extension requires search
version 1; absent support is an explicit skip under both profiles. These checks
do not authenticate a reviewer's label or certify host approval workflows.

When `history_ranking: {version: 1}` is declared, history-ranking cases check
relevance scores against literal values from an independent reference, ties,
newest order, turn-time bounds, erased turns and invalid requests on
`conversation/search_turns`, and that requests without the new fields keep the
version 1 answer. Fixtures are ASCII, where every backend folds alike. Absent
support is an explicit skip under both profiles. A service may refuse an
over-broad relevance request with `value_error` (the reference server's
`NEOSIAN_SEARCH_MAX_CANDIDATES`); the suite's small fixtures stay under any
sensible bound, so it does not test one.

When `size_paging: {version: 1}` is declared, size-paging cases check that the
pages of `conversation/search_turns` (every order the service declares) and
`conversation/read_turns` reassemble the answer the service gives without the
new fields, that each page holds every whole turn that fits under
`page_bytes` and at least one, that a continuation binds its request, that
invalid paging fields refuse, and that a relevance continuation refuses once
its candidates changed. Turns are measured as compact JSON over ASCII
fixtures. Absent support is an explicit skip under both profiles. Spooled
answers need no case of their own: every existing case reads them whole.

When `turn_records: {version: 1}` is declared, turn-records cases write a
turn in parts through `conversation/write_records` (probe, opening, repeats
under other part boundaries, seal, discard, every `draft_*` conflict with
its details, sixteen open drafts, strict decoding, the body ceiling as `413`
before parsing and a stated total no store keeps), read it back through
`conversation/read_records` (the same records for a turn stored whole and
one written in parts, every view, heads and tails, a piece fetched whole,
pages checked for their bound and for being full, continuations that bind
their selection, a redacted turn's header, the answer's bound measured on
its bytes), search it through `conversation/search_records` (both orders
with literal scores from an independent reference, `kinds`, time bounds,
`limit`, an excerpt window that starts inside a long record, the answer's
bound measured on its bytes, what never matches) and move a conversation through
`store/restore_records` (two parts and a commit, the probe and its position,
repeats, every restore conflict with its details, an occupied target at the
first part and at the commit, the discard, the empty commit, actors and
times stored as sent, and the unit digest computed here). Each answer's
`stored_bytes` and `freed_bytes` are computed from the published size rule
and the request alone. The record builders and the size rule are shared with
`../records/`; they import no product code.

These cases are the backend-neutral subset of the wire. They plant no file,
rely on no server setting and stay inside the value range every store
accepts (ASCII text, no NUL, numbers within 32 bits where a store keeps
them so, no turn above 65,536 bytes, the least bound a store may set on a
turn). They read an answer's body however the service frames it, require no
`Content-Length`, assert a refusal's status and code and only the `details`
and message prefixes the wire document publishes, parse times and accept two
turns stamped at one instant. Two checks depend on the deployment and run
only on the local file and Postgres fixtures: the status line a raw socket
reads when a body is declared and not sent, and the refusal of the five
routes on a store without records. What depends on a setting or on planted state (a stale draft
swept, the candidate bound, the store's turn bound lowered, the long-turn
refusal and `omitted` on the legacy routes) is covered for the reference
backends in `../records/` and `../postgres/`. `store/sweep_records` is
store-wide, so the suite only previews it: it sends `write: false` or no
`write` at all, checks the answer's shape, order and refusals, and never
removes anything. Absent support is an explicit skip under the full and base
profiles; the records profile below requires it. A service that does not
declare the capability owes the five routes nothing: the suite sends it none
of them, and a wire 6 service built before they existed, which answers
`404`, conforms under the full and base profiles. The reference backends
answer `agent_configuration_error` there; that is their own behavior, checked
on the local fixtures and pinned in `../records/`.

From a checkout with the development dependencies:

```sh
cargo build --locked
make test-storage-contract                     # two FileStore server processes
make test-storage-contract backend=postgres    # NEOSIAN_TEST_POSTGRES_DSN required
make test-storage-contract backend=http        # an already running test service
make test-storage-contract profile=records     # the same, on a store that keeps records
```

File mode gives both processes one temporary home. Postgres mode applies the
shipped generation-4 SQL to a new uniquely named schema, runs two processes on
it and drops only that schema on exit. Under `profile=records` the file
fixture runs `neosian records enable` on that home and the Postgres fixture
also applies records extension 1 before either process starts; under the
other profiles neither does, so the default runs exercise a store without
records. A missing binary or DSN fails explicitly.
The server itself never applies DDL. The Postgres fixture needs psycopg; the
HTTP mode needs no database driver or binary.

For HTTP mode, set `NEOSIAN_TEST_SERVER_URL` and
`NEOSIAN_TEST_SERVER_TOKEN`. Use an unconstrained test token: the kit checks
invalid scopes and whole-store portability as well as valid namespaced calls.
An optional `NEOSIAN_TEST_SERVER_PEER_URL` names a second worker serving the
same store with the same bearer identity. With no peer URL, concurrent requests
go to the primary endpoint; that run does not establish coordination between
separate service processes. Local file and Postgres runs always use two workers.

Run against a disposable test service. Each case writes under its own random
`test:contract-<id>` scope and `contract-<id>` conversation, plus derived names.
The kit exercises deletion, irreversible content erasure and restore there.
It leaves history behind on external services; disposing of their storage is
the operator's job. It never sweeps or erases unrelated scopes or conversations:
the store-wide sweep of turn records is only previewed. The turn-records
cases discard the drafts and restores they leave open where the case allows
it; an interrupted run can leave some, which the service's own sweep removes
once they are stale.

The default **full** profile requires optimistic concurrency, paging, turn
erasure and whole-store portability. Missing capabilities fail the run before
the cases proceed. For a service intentionally implementing the base wire:

```sh
make test-storage-contract backend=http profile=base
```

The **records** profile is the full profile plus a required
`turn_records: {version: 1}` on the primary and on the peer. A service that
does not declare it fails the run before the cases proceed; the profile
never passes by skipping the turn-records cases, and on the local fixtures
it fails any case that skips. `make test` runs this profile on files after
its default run, and `make test-postgres` runs it on Postgres after its
schema-4 run:

```sh
make test-storage-contract backend=http profile=records
```

The **base** profile still checks expected-version mismatches, memory erasure
and every required operation. It exercises every declared optional capability,
checks the refusal for each undeclared one, and reports the optional behavioral
cases it skipped. `supports_optimistic_concurrency=false` means no cross-worker
guarantee; it never permits ignoring `expected_version` on a serial write.

Report the neosian revision, backend/deployment, profile, number passed/skipped
and whether a separate worker was exercised. A base-profile pass is not a
full-profile pass, and a full-profile pass with the turn-records cases
skipped is not a records-profile pass. Passing this wire suite is evidence for these cases, not
certification of a service's authentication system, operational durability,
custom physical format or all backend races. The separate
[FileStore corpus](../file/README.md) plants malformed files and tests basic
containment. `../postgres/` plants malformed database rows; `../replay/`
preserves the frozen v1 operator, client, hook and HTTP MCP expectations.

The runner can also live in a test-only environment, with no neosian package:

```sh
uv run --isolated --no-project --python 3.12 \
  --with pytest --with pytest-timeout \
  --with jsonschema --with PyYAML \
  python -m pytest --confcutdir=tests/contract tests/contract/storage \
  -m contract --storage-backend=http -q -rs
```

The fixture and tests import neither the server's implementation nor its codecs.
The source SQL asset is used only to provision the reference Postgres fixture;
expected wire records and behavior remain independent.
