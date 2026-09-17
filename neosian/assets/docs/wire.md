---
title: "The wire: the state process's HTTP contract"
summary: The eighteen /v1/ routes, the envelope, paging, the JSON shapes and WIRE_VERSION
---

# The wire

`neosian serve` puts the two storage ABCs on a port. This page is the
contract a client in any language can be written against: the
handshake, the eighteen `/v1/` routes with their request and response
keys, the error envelope, paging, the JSON shapes, and the history of
`WIRE_VERSION`. Every literal here is pinned by a test that drives the
server with raw JSON and never through `RemoteStore`
(`tests/unit/server/test_wire_golden.py`, `test_wire_probes.py`).

The wire mirrors the ABCs one method to one route and carries nothing
else: no audit route (the shell composes `neosian audit` from
`memory/history`, `memory/redactions` and `conversation/read_turns`),
no compaction, no reflection. Those need a model, and the process
serves storage only. It is the process's API, versioned with the
library rather than an ECOSYSTEM seam: `WIRE_VERSION` rides the
handshake, and a client that speaks another version is refused before
any store call.

## The handshake

`GET /v1/capabilities`, authenticated, answers six keys:

```json
{"wire_version": 4, "neosian_version": "<installed>", "backend": "FileStore",
 "supports_optimistic_concurrency": false, "pageable": true,
 "client": "client:default"}
```

`backend` is the class serving the store, `pageable` whether its
listings answer pages (below), and `client` the actor the presented
token makes the caller. `GET /health` is the one unauthenticated route
and answers `{"status": "ok"}`, liveness only.

## Auth

Every request but `/health` carries `Authorization: Bearer <token>`,
compared against `NEOSIAN_SERVE_TOKEN`: one token, or a table
`actor=token,actor=token` so the process records who wrote
(`neosian docs topology`, "Who wrote what"). A miss is a plain `401`
with `WWW-Authenticate: Bearer` and no JSON body.

An actor may carry an allowance, `client:alice@user:alice+alice-=tok`:
two literal prefixes matched with `startswith` against the `scope` and
`conversation_id` a request names. A request outside them is `403` in
the envelope under the code `forbidden`. A constrained token is refused
`/mcp` and all four `store/*` routes outright, whatever its body names.
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
| `conversation/append_projections` | `conversation_id`, `entries` req | `{}` |
| `conversation/read_projections` | `conversation_id` req; `after`, `limit` | `entries`, `next_after` |

### Store

| route | request | response |
|---|---|---|
| `store/scopes` | `{}` | `scopes` (sorted names) |
| `store/conversations` | `{}` | `conversations` (sorted ids) |
| `store/restore_scope` | `scope`, `documents`, `versions`, `redactions` req | `{}` |
| `store/restore_conversation` | `conversation_id`, `turns`, `projections` req | `{}` |

A restore is verbatim: the rows' own actors are kept and the caller's
client is not stamped. The target must be empty, else `memory_conflict`
or `agent_conversation_conflict` with `reason: "target_occupied"`. A backend
that cannot be moved whole answers every `store/*` route with
`agent_configuration_error`.

## Paging

No listing answers more than 500 rows. The memory listings take an
opaque `cursor` and answer `next_cursor`, `null` on the last page; the
conversation reads take `after` (a turn number, `0` or absent from the
start) and answer `next_after`, `null` on the last page. A `limit` of
500 or less is the ABC's exact answer; `limit: 0` is an empty page with
no continuation; a negative `limit` or `after` is `value_error`. A
`read_projections` page ends on a whole turn. A host store served
without `Pageable` answers whole with `next_cursor: null` and refuses a
`cursor` by name; the handshake's `pageable` says which.

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
  `actor` (a string or `null`; absent on a restore reads as `null`).
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

A narrowing (a parameter refused that was once let through) never moves
the version; a new route or a new key does. `RemoteStore.connect`
refuses a mismatch by name before any store call.

## Beside the wire

`/mcp`, present when the process is started with mounts, speaks the MCP
specification over streamable HTTP and is not covered here
(`neosian docs mcp`). The Python client is `RemoteStore`
(`neosian docs topology`); a client in another language implements
this page.
