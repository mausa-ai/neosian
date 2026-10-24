---
title: "The wire: the state process's HTTP contract"
summary: The twenty-one /v1/ routes, the envelope, paging, the JSON shapes and WIRE_VERSION
---

# The wire

`neosian serve` exposes durable state through wire 6. This page documents
its twenty-one storage POST routes and two GET routes. Literal fixtures and
independent raw HTTP, CLI and MCP cases pin behavior without a product SDK.

Machine contracts live in `assets/contracts/`: `wire-v6.openapi.json` is
OpenAPI 3.1.1 and `state-v1.schema.json` is JSON Schema 2020-12. Keep them
together for offline references. Their IDs are identifiers, not served
endpoints: `/openapi.json` and `/contracts` are not routes. Print them with
`neosian schema wire` and `neosian schema state`, or download the release's
contracts archive. Typed inputs and canonical outputs are described; not
every legacy decoder coercion is a schema promise.

The built-in FileStore and Postgres backends implement the whole wire.
Custom storage is an independent conformance-tested HTTP service. Backend
capabilities determine paging, erasure and optimistic concurrency.
`make test-storage-contract` exercises those behaviors; `make test` adds
physical file failures, frozen v1 compatibility cases and product doors.

The wire serves storage, not an agent runtime. `audit` is composed from
history and redaction reads; maintenance and semantic reflection are not
wire routes. Product version 2.0.0 retains `wire_version: 6` and format 1.

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
operation's, by name. A body that is not JSON, or not an object, is
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

`code` is the stable error identifier and `details` its typed fields.
`neosian schema errors` lists active and reserved identifiers.
Two codes exist only on the wire: `value_error`, for a mis-typed or
missing parameter and for invalid store arguments (an empty message
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
`value_error`. A backend without paging answers whole with
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

A narrowing of accepted legacy input does not move the wire version; an
added route or response key does. The binary refuses a different advertised
wire version before any storage call.

## Beside the wire

`/mcp` is available when the process starts with mounts. It uses authenticated
streamable HTTP and the initialize/initialized handshake through protocol
2025-11-25. The retired Python server's 2026 single-exchange protocol is not
part of v2. See `neosian docs mcp` for lifecycle and tool scope boundaries.
