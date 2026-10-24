---
title: "Migrating from v1"
summary: The v2 product boundary, existing homes and clients, metadata conversion and Postgres connection settings
---

# Migrating from v1

Version 2 is the memory and context core for the agent you already use.
It ships one Rust binary with CLI, MCP and HTTP interfaces. There is no
dedicated SDK, Rust embedding API, Python distribution, agent runtime,
provider catalog, chat/TUI, playground, runtime guardrail system or agent
evaluation runner. `configure`, `chat`, `playground`, `eval`, `reflect`
and maintenance's `--model` option are retired. Bare `neosian` prints help.
Use your agent's own model/runtime facilities; `upkeep` and `handoff` are
skills served to that agent. V1 releases remain in repository history;
there is no maintained v1 product line.

## Homes, histories and clients

Profiles are an explicit opt-in, with no history rewrite. Bind existing exact
scope strings to a stable workspace UUID, select old conversation IDs or their
authorized prefix for read access, and reinstall clients with `--profile NAME`.
New captured IDs use the workspace namespace; legacy IDs remain intact.
Import alone never activates a profile or permits recording. New profile
capture is off; profile-free hooks keep their previous behavior. HTTP sources
must advertise scoped authorization before a profile can use them. See
`neosian docs profiles` for the local-binding and sharing migration.

Memory, turns and mailbox records remain format 1. Postgres schema remains
4 and the storage HTTP protocol remains wire 6. Version numbers, actors,
redaction skeletons, projections and unknown JSON metadata are preserved.
Message and tool-call `extra` fields retain provider-specific data without
loading a provider client. Existing normal homes and portable archives need
no conversion. Readers refuse unsupported or malformed records. Memory
mutations and turn erasure refuse unsupported affected format markers before
changing content. Malformed records require explicit operator repair.

Stop all writers and back up the home before upgrading. FileStore's root
lock coordinates cooperating local writers; it does not protect against
uncooperative filesystem edits or provide network filesystem transactions.
Old logs with malformed envelopes or escaping symlinks must be repaired
explicitly. Do not bypass the refusal by deleting history.

Install the binary, then run its absolute path with `setup --write` to
refresh client registrations and generated adapters. Old registrations
that invoke `python -m neosian` still invoke v1 until replaced. `status`
reports competing executables and registrations. The installer preserves
an existing command on PATH. Remove the old Python installation with its
original package manager after the new registrations work. The binary
leaves old `config.toml` credential values untouched and never uses or
displays them; remove obsolete provider credentials separately if desired.

HTTP MCP uses a negotiated session handshake (2025-03-26, 2025-06-18 or
2025-11-25). The retired Python server's 2026 single-exchange protocol is not
supported by v2. Use a client implementing one of the listed session protocols;
`neosian docs mcp` documents initialization, sessions and content negotiation.

## YAML metadata

FileStore preserves unknown envelope lines on ordinary local updates.
HTTP and portable operations support scalar YAML and single-line JSON
arrays/objects. Arbitrary block YAML, aliases and folded strings require
explicit conversion before crossing those boundaries. Unsupported metadata
is refused with `memory_format_unsupported`; wire writes and renames
preflight their response metadata before changing a document.

The release's `migrate_v1_metadata.py` is an optional, one-time tool, not a
Python product dependency. It requires Python 3.12+ and PyYAML. With every
writer stopped, run it first without `--write`, inspect its list, then apply:

```sh
uv run --no-project --with PyYAML python migrate_v1_metadata.py /path/to/home
uv run --no-project --with PyYAML python migrate_v1_metadata.py /path/to/home --write
```

It validates the entire selection before writing, creates private exclusive
`.md.v1-backup` copies, and atomically replaces each converted document.
Bodies and historical journals stay byte-identical. Nested JSON-compatible
values retain their types. Non-string mapping keys, sets, non-finite numbers,
custom YAML values and recursive aliases are refused for an explicit operator
decision. Existing backup files also stop the run. An I/O failure can leave
some documents converted; backups permit recovery. A successful rerun is a
no-op. Keep backups outside public exports and remove them only after review.

## Postgres DSNs

Supply an explicit `NEOSIAN_POSTGRES_DSN` as a Postgres URI or keyword/value
string, including host, user, password when needed and database. The binary
uses tokio-postgres, not libpq: implicit `PGHOST`, `PGUSER`, `PGPASSWORD`,
service files and `.pgpass` are not a substitute for this DSN. Unsupported
options are refused with value-blind connection guidance. No DSN is logged.

Supported TLS modes are `disable`, `prefer` and `require`. TLS always verifies
the certificate and hostname. For a previous `sslmode=verify-full` connection,
use `sslmode=require` and, for a private CA, set `PGSSLROOTCERT` to its PEM
bundle. `verify-ca`, libpq's `sslrootcert` DSN option and client certificate
options are refused; mutual TLS is not supported. Do not weaken a required
TLS connection to `prefer`, which permits a non-TLS server. Unknown options
and invalid certificate bundles fail before any storage mutation.

Apply schema changes explicitly with `neosian schema postgres --schema NAME`.
Opening a store never applies DDL. The command emits the same schema-4
statements as v1; an operator controls applying them and the database account.

## Custom storage and application callers

Replace Python `Agent`, `Conversation`, `MemoryStore`, `ConversationStore`,
`RemoteStore`, `build_app` and other product imports with your application's
own runtime plus neosian's MCP or HTTP interface. Custom backends implement
wire 6 in an independent service and pass the published conformance runner.
There is no automatic conversion of Python subclass implementations.
Use `neosian docs stores` and `neosian schema wire` for the contract.

Error identifiers remain reserved forever. `neosian schema errors` lists
each active and reserved v1 identifier; conversation failures retain their
`agent_conversation_*` names and configuration failures retain
`agent_configuration_error`. A prefix alone never determines retirement.
