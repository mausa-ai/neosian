---
title: "The wire: the state process's HTTP contract"
summary: The twenty-one /v1/ routes, the envelope, paging, the JSON shapes and WIRE_VERSION
---

# The wire

`neosian serve` puts the two storage ABCs on a port. This page is the
contract a client in any language can be written against: the
handshake, the twenty-one `/v1/` routes with their request and response
keys, the error envelope, paging, the JSON shapes, and the history of
`WIRE_VERSION`. Every literal here is pinned by a test that drives the
server with raw JSON and never through `RemoteStore`
(`tests/unit/server/test_wire_golden.py`, `test_wire_probes.py`), and the
differential tier replays the same exchanges against the Python and the
Rust state process over one home (`tests/differential/serve_cases.py`):
the two answer alike and leave the same home. The remote hook and MCP
corpus also runs through all four Python/Rust client-server pairings
(`tests/differential/test_remote.py`), with separate multi-page, TLS and
version-skew probes. The Rust client implements the operations the
recording, MCP, memory and operator doors (including messages, search and
continue) call; Python `RemoteStore`
implements the whole wire.

Machine-readable contracts live in `neosian/assets/contracts/` in the
source tree and wheel: `wire-v6.openapi.json` describes the 21 storage
POSTs and the two GETs using [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html).
Its adjacent `state-v1.schema.json` defines canonical records using
[JSON Schema 2020-12](https://json-schema.org/draft/2020-12/json-schema-core).
Keep the files together to resolve references offline; their schema IDs
are identifiers, not endpoints served by `neosian serve`. No request to
`/contracts` or `/openapi.json` is provided. The schemas describe supported
typed inputs and canonical outputs, not every legacy decoder coercion.

`make test-contract` drives the binary through raw HTTP, CLI and MCP,
validating successful storage requests and responses against those files.
Its versioned fixtures and expected results import no Python product code.
Its storage subset also checks files, Postgres and independently operated
test services through `make test-storage-contract` (`neosian docs stores`).
The FileStore corpus also plants malformed physical records and verifies
refusals, numbering, private modes and basic containment. These complement
the existing conformance and differential suites; Postgres corruption,
deeper races, TLS, HTTP MCP, hooks, adapters and full operator coverage
still retain their existing checks.

Every route listed here is served by both implementations. The Rust
binary and the container image serve FileStore or Postgres; the Python
server also serves a host's store through `build_app`. The image
contains the Rust server since 1.11.0. Backend capabilities in the
handshake, rather than the implementation language, decide whether a
client can page or erase. `WIRE_VERSION` remains 6.

The wire mirrors the ABCs one method to one route and carries nothing
else: no audit route (the shell composes `neosian audit` from
`memory/history`, `memory/redactions` and `conversation/read_turns`),
no compaction, no reflection. Those need a model, and the process
serves storage only. It is the process's API, versioned with the
library rather than an ECOSYSTEM seam: `WIRE_VERSION` rides the
handshake, and a client that speaks another version is refused before
any store call.

## The handshake

`GET /v1/capabilities`, authenticated, answers seven keys:

```json
{"wire_version": 6, "neosian_version": "<installed>", "backend": "FileStore",
 "supports_optimistic_concurrency": true, "pageable": true, "erasable": true,
 "client": "client:default"}
```

`backend` is the class serving the store, `pageable` whether its
listings answer pages (below), `erasable` whether its turns can be
redacted (the two erasure routes below), and `client` the actor the
presented token makes the caller. `GET /health` is the one unauthenticated route
and answers `{"status": "ok"}`, liveness only.

The capability fields are independent. A client requiring concurrent
version fences checks `supports_optimistic_concurrency`; a client following
memory cursors checks `pageable`; turn erasure requires `erasable`.
Whole-store portability has no handshake field in wire 6: all four
`store/*` routes require it and answer `agent_configuration_error` when
the backend lacks it. The built-in file and Postgres backends support
these capabilities. A custom HTTP service must reproduce their declared
semantics and failures, including attribution and scope allowances;
returning the same JSON shapes alone is insufficient.

## Auth

Every request but `/health` carries `Authorization: Bearer <token>`,
compared against `NEOSIAN_SERVE_TOKEN`: one token, or a table
`actor=token,actor=token` so the process records who wrote
(`neosian docs topology`, "Who wrote what"). A miss is a plain `401`
with `WWW-Authenticate: Bearer` and no JSON body.

An actor may carry an allowance, `client:alice@user:alice+alice-=tok`:
two literal prefixes matched with `startswith` against the `scope` and
`conversation_id` a request names, and against every id in a search's
or a trail read's `conversations` list. A request outside them is `403`
in the envelope under the code `forbidden`. A constrained token is
refused `/mcp` and all four `store/*` routes outright, whatever its body
names, and a search or a trail read that names no `conversations` (the
whole store) the same way.
A token with no allowance reaches everything.

## Requests

Every route is a `POST` with a JSON object body; the parameters are the
ABC method's, by name. A body that is not JSON, or not an object, is
`400`. An absent optional parameter and `null` are the same thing.
Every parameter is typed at the door, nested objects included: a string
where an integer belongs is `400` naming the parameter, and a JSON
boolean is never an integer. Keys the route does not know are ignored.
The `Content-Type` header is not read. A body over 64 MiB is `413`,
checked inside the bearer gate, so an unauthenticated oversize body is
still `401`.

## The envelope

A refusal is one shape at `400`, `403` or `413`:

```json
{"error": {"code": "memory_conflict",
           "message": "Memory conflict on 'notes/a' in scope 'user:a': version_mismatch",
           "details": {"scope": "user:a", "path": "notes/a", "reason": "version_mismatch",
                       "expected_version": 5, "actual_version": 1}}}
```

`code` is the library's error code and `details` its typed fields, so a
client rebuilds the exact exception a `FileStore` caller would catch.
Two codes exist only on the wire: `value_error`, for a mis-typed or
missing parameter and for the ABCs' own `ValueError` (an empty message
list, a negative `after`), and `forbidden`, for the allowance. A
malformed nested object (a message without `role`, an archive document
without `actor`) is `value_error` with the message
`malformed request: 'role'`. A `500` carries no envelope and is the
driver's problem, never a store answer.

## The routes

A response is the return value under one key; a listing adds its
continuation beside it. `req` marks a required parameter.

### Memory

| route | request | response |
|---|---|---|
| `memory/read` | `scope` req, `path` req | `document` (object or `null`) |
| `memory/write` | `scope`, `path`, `content` req; `actor`, `expected_version` | `document` |
| `memory/delete` | `scope`, `path` req; `actor` | `deleted` (bool) |
| `memory/rename` | `scope`, `src`, `dst` req; `actor` | `document` |
| `memory/list_documents` | `scope` req; `prefix`, `cursor`, `limit` | `entries`, `next_cursor` |
| `memory/versions` | `scope`, `path` req; `cursor`, `limit` (default 50) | `versions`, `next_cursor` |
| `memory/redact` | `scope` req; `path`, `actor` | `count` (int) |
| `memory/history` | `scope` req; `since`, `cursor`, `limit` | `versions`, `next_cursor` |
| `memory/redactions` | `scope` req; `since`, `cursor`, `limit` | `redactions`, `next_cursor` |

A write with no `actor` is recorded under the token's client
(`client:default`); with `actor: "me"` under `client:default/me`.
`redact` with no `path` redacts the whole scope and records a redaction
whose `path` is `null`. `versions` and `history` answer newest first.

### Conversation

| route | request | response |
|---|---|---|
| `conversation/append_turn` | `conversation_id`, `messages` req; `actor` | `turn` |
| `conversation/read_turns` | `conversation_id` req; `after`, `limit` | `turns`, `next_after` |
| `conversation/last_turn_number` | `conversation_id` req | `turn` (int, 0 when none) |
| `conversation/search_turns` | `query` req; `conversations` (a list of ids), `limit` (default 50, at most 500) | `turns` |
| `conversation/append_projections` | `conversation_id`, `entries` req | `{}` |
| `conversation/read_projections` | `conversation_id` req; `after`, `limit` | `entries`, `next_after` |
| `conversation/redact_turns` | `conversation_id` req; `through`, `turns` (a list of turn numbers), `actor` | `count` (int) |
| `conversation/turn_redactions` | `conversations` (a list of ids), `since`, `limit` (default 50, at most 500) | `redactions` |

`search_turns` (`WIRE_VERSION` 5) answers the turns whose text holds
every whitespace-split term of `query` as a case-insensitive substring,
newest first: a turn's text is its messages' text, each tool call as
its name and compact JSON arguments, and its tool results, with no role
labels. `conversations` absent or `null` is every conversation the
store holds; a list narrows to those ids and `[]` answers `[]`. The
answer is bounded and never pages: `limit` above 500 is `value_error`
("a search answers at most 500 turns; narrow the query"), `limit: 0`
and a blank `query` are `value_error` too. Each turn is the
`read_turns` shape.

`redact_turns` and `turn_redactions` (`WIRE_VERSION` 6) are the eraser
(`neosian docs stores`). `redact_turns` blanks the selected turns of one
conversation and every projection entry covering them, keeps each
turn's number, `created_at` and `actor`, and answers the count matched,
already-redacted turns included, so repeating it changes nothing.
`through` and `turns` both absent is every turn; `through: n` is turns 1
to n; `turns` names the numbers; both given, or a number below 1, is
`value_error`; an unknown conversation or number matches nothing. The
act is recorded under the token's client like a write (`actor: "ops"`
records `client:default/ops`). `turn_redactions` answers the trail
newest first and is bounded like a search: `limit` above 500 is
`value_error`, `conversations` narrows as it does for a search, and a
redacted turn never answers a search again. A backend whose `erasable`
is `false` answers both routes with `agent_configuration_error`.

### Store

| route | request | response |
|---|---|---|
| `store/scopes` | `{}` | `scopes` (sorted names) |
| `store/conversations` | `{}` | `conversations` (sorted ids) |
| `store/restore_scope` | `scope`, `documents`, `versions`, `redactions` req | `{}` |
| `store/restore_conversation` | `conversation_id`, `turns`, `projections` req; `redactions` | `{}` |

A restore is verbatim: the rows' own actors are kept and the caller's
client is not stamped. The target must be empty, else `memory_conflict`
or `agent_conversation_conflict` with `reason: "target_occupied"`. A backend
that cannot be moved whole answers every `store/*` route with
`agent_configuration_error`.

## Paging

Memory listings take an opaque `cursor`, cap each page at 500 rows and
answer `next_cursor`, `null` on the last page. Cursors are specific to
their listing method; passing a document-list cursor to `versions` is
`value_error`. A host store served without `Pageable` answers whole with
`next_cursor: null` and refuses a `cursor` by name; the handshake's
`pageable` says which.

Conversation reads take `after` (a turn number, `0` or absent from the
start). With an omitted limit or a limit above 500 they page automatically
and answer `next_after`. An explicit `limit` of 500 or less is a total
bound and answers `next_after: null` even when more rows exist. Automatic
projection paging ends on a whole turn and can exceed the nominal 500
rows when one turn has more entries; an explicit total bound can cut
that group. On both memory and conversation reads, `limit: 0` answers
no rows or continuation; a negative `limit` or `after` is `value_error`.
`conversation/search_turns` is not a listing and does not page: its
`limit` is a bound, 500 at most.

## The JSON shapes

Timestamps are ISO-8601 with a `Z` suffix on the way out; any offset is
accepted on the way in and normalised to UTC; a naive timestamp is
refused.

- **document**: `scope`, `path`, `content`, `version`, `created_at`,
  `updated_at`, `actor`, `redacted`, `extra` (an object, `{}` when
  empty). On a restore, `extra` may be absent; every other key is
  required.
- **entry** (a listing row): `path`, `version`, `created_at`,
  `updated_at`, `redacted`.
- **version** (a history row): `path`, `version`, `action` (`created`,
  `modified`, `deleted`), `content`, `actor`, `created_at`, `redacted`.
  A redacted row keeps its skeleton with `content: ""` and
  `redacted: true`. Every key is required on a restore.
- **redaction**: `path` (or `null` for a scope), `actor`, `created_at`,
  `count`.
- **turn**: `conversation_id`, `turn`, `messages`, `created_at`,
  `actor` (a string or `null`; absent on a restore reads as `null`),
  `redacted` (absent on a restore reads as `false`). A redacted turn
  carries `messages: []` and `redacted: true`; no other turn carries an
  empty message list, and a restore refuses one.
- **turn redaction** (a trail row): `conversation_id`, `turns` (the
  numbers blanked, ascending integers from 1), `actor`, `created_at`.
- **projection**: `turn`, `kind` (`log`, `digest`, `epoch`), `text`,
  `span` (absent reads as `1`).

A **message** is the public codec's shape, the same object the FileStore
turn log holds:

```json
{"role": "assistant", "content": "hi", "reasoning": null,
 "tool_calls": [{"id": "c1", "name": "memory", "arguments": {"command": "view"}}],
 "tool_call_id": null}
```

`role` is the one required key; `content` is a string, `null`, or a
list of blocks (`{"type": "text", "text": ...}`, and `image`,
`document` and `compaction` blocks with their own keys, an unknown
`type` refused). A tool call's `id` and `name` are strings and its
`arguments` an object, absent reading as `{}`; a string of JSON there
is refused. `extra` on a message or a tool call is carried verbatim and
omitted from the answer when empty. Keys the codec does not know are
dropped, not stored.

## WIRE_VERSION

| version | release | what changed |
|---|---|---|
| 1 | 0.80.0 | the process: twelve routes, the handshake, the envelope |
| 2 | 0.83.0 | `memory/history`, `memory/redactions`, the turn `actor`, the handshake's `client` |
| 3 | 0.89.0 | the four `store/*` routes |
| 4 | 1.0.0rc8 | every listing answers a page; the handshake's `pageable` |
| 5 | 1.0.0rc20 | `conversation/search_turns`; a constrained token's `conversations` list |
| 6 | 1.8.0 | `conversation/redact_turns`, `conversation/turn_redactions`; the turn's `redacted`; the archive's `redactions`; the handshake's `erasable` |

A narrowing (a parameter refused that was once let through) never moves
the version; a new route or a new key does. `RemoteStore.connect`
refuses a mismatch by name before any store call.

## Beside the wire

`/mcp`, present in both implementations when the process is started with
mounts, speaks MCP over streamable HTTP. The Rust server supports the
handshake versions from 2024-11-05 through 2025-11-25 and refuses a newer
protocol header with `-32022 Unsupported protocol version`; neosian's
MCP client falls back to the handshake. The Python server also serves
the 2026-07-28 single-exchange protocol through its SDK. This transport
is separate from the storage wire (`neosian docs mcp`). The Python client
is `RemoteStore` (`neosian docs topology`); a client in another language
implements this page.
