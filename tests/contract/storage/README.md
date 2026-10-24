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

From a checkout with the development dependencies:

```sh
cargo build --locked
make test-storage-contract                     # two FileStore server processes
make test-storage-contract backend=postgres    # NEOSIAN_TEST_POSTGRES_DSN required
make test-storage-contract backend=http        # an already running test service
```

File mode gives both processes one temporary home. Postgres mode applies the
shipped generation-4 SQL to a new uniquely named schema, runs two processes on
it and drops only that schema on exit. A missing binary or DSN fails explicitly.
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
the operator's job. It never sweeps or erases unrelated scopes or conversations.

The default **full** profile requires optimistic concurrency, paging, turn
erasure and whole-store portability. Missing capabilities fail the run before
the cases proceed. For a service intentionally implementing the base wire:

```sh
make test-storage-contract backend=http profile=base
```

The **base** profile still checks expected-version mismatches, memory erasure
and every required operation. It exercises every declared optional capability,
checks the refusal for each undeclared one, and reports the optional behavioral
cases it skipped. `supports_optimistic_concurrency=false` means no cross-worker
guarantee; it never permits ignoring `expected_version` on a serial write.

Report the neosian revision, backend/deployment, profile, number passed/skipped
and whether a separate worker was exercised. A base-profile pass is not a
full-profile pass. Passing this wire suite is evidence for these cases, not
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
