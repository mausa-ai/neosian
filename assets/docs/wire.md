---
title: "The wire: the state process's HTTP contract"
summary: Storage routes, optional memory search, authorization, paging and wire formats
---

# The wire

`neosian serve` exposes durable state through wire 6. This page documents
its twenty-one base storage POST routes, optional search, change and turn-records routes, and two GET routes. Literal fixtures and
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

`GET /v1/capabilities`, authenticated, answers these base keys:

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

The reference backends also advertise `"memory_search": {"version": 1}`,
`"memory_ranking": {"version": 1}` and `"memory_passages": {"version": 1}`.
This optional, separately versioned operation is independent of legacy
`pageable`. Clients check it before calling `memory/search`; absence means
unsupported, and the binary refuses search without downloading documents as
a fallback. Existing wire-6 routes, stored formats and schema 4 are unchanged.
The conformance runner exercises declared search support on both full and
base profiles, and reports skipped search cases when it is absent.

The optional `"knowledge_policy": {"version": 1}` capability adds governance
filtering to that operation. It requires memory-search version 1. The reference
backends advertise both; clients refuse filtered search on an older service.

The optional `"memory_ranking": {"version": 1}` capability adds relevance and
newest orders and date bounds to memory search, and `"memory_passages":
{"version": 1}` adds matched passages; both require memory-search version 1.
The reference backends advertise them. Clients send their fields only to a
service that declares them.

The optional `"history_ranking": {"version": 1}` capability adds relevance and
newest orders and turn-time bounds to `conversation/search_turns`. It does not
depend on memory search. The reference backends advertise it, and clients send
its fields only to a service that declares it.

The optional `"size_paging": {"version": 1}` capability adds byte-bounded pages
to `conversation/search_turns` and `conversation/read_turns` (below). It does
not depend on the other capabilities. The reference backends advertise it for
clients and hosts that want a ceiling on what one response carries. The
binary's own remote client first sends 2.16.0's request. If the server
advertises size paging, the client can retry in pages below the server's
1 MiB in-memory spool. It retries when its own response budget is exceeded
or the server returns `value_error` with a message beginning `resource limit
NEOSIAN_MAX_TEMP_BYTES exceeded: ` or `resource limit NEOSIAN_MAX_RESPONSE_BYTES
exceeded: `. A second size refusal or a stale relevance continuation reports
the original size refusal. Other failures report their own error.

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
names, and a conversation search or a trail read that names no `conversations` (the
whole store) the same way.
A token with no allowance reaches everything unless the opt-in grants below
narrow it. Ignored body fields cannot authorize an unbounded search or trail
read: those routes check the actual `conversations` array.

### Exact-scope grants for portable profiles

Set `NEOSIAN_SERVE_GRANTS` to an operator-owned TOML file. Version 1 grants
intersect the existing token table; missing actors or permissions deny access.
Token values stay exclusively in `NEOSIAN_SERVE_TOKEN`. For example, with
an authenticated `client:reader` token actor:

```toml
grant_format = 1
[[clients]]
actor = "client:reader"
[[clients.scopes]]
scope = "workspace:team"
read = true
write = false
[[clients.conversations]]
prefix = "w-11111111222243338444555555555555-"
read = true
append = false
```

Memory scopes match exactly, unlike legacy token prefixes. A conversation
grant requires either a literal `prefix` or nonempty `ids = [...]`. `read`
permits reads, memory search, versions, listings and history; `write` permits memory writes,
delete and rename; `append` permits both turns and projections. Permissions
are independent and default false. Erasure also requires `operator = true`
on the client entry. That flag grants all four whole-store routes, including
unrestricted enumeration and restore subject to legacy allowances; reserve
it for trusted administrators. Non-operators cannot use those routes.
Even an operator's conversation search/trail request names an explicitly authorized
`conversations` array. `[]` remains an empty search, never a global read.

Checks precede store access and apply to raw HTTP callers as well as profile
clients. Editing a profile or forging the request's attribution cannot widen
the token's permission. Grant files reject unknown fields, unsupported format,
duplicate actors/scopes and actors absent from the token table before opening
the store. Files are bounded to 1 MiB and loaded once at startup. Restart the
server after changing grants or revoking/rotating tokens.

With grants enabled the handshake adds this separately versioned optional
capability: `"authorization": {"version": 1, "mode": "scoped"}`. It reports
enforcement, not a permission inventory. Portable HTTP profiles require it
and refuse an older/unscoped backend. Legacy clients keep wire 6. The profile
and grants schemas print with `neosian schema profile` and `neosian schema
grants`. Custom services advertising this capability must enforce the same
restrictions; the existing base/full storage runner alone does not certify
authorization. Reference-server adversarial tests are in
`tests/contract/test_profile_http.py`.

All `/mcp` access is refused when grants are enabled. Use the local
`neosian mcp --profile NAME` stdio bridge to the granted storage routes.

## Requests

Every route is a `POST` with a JSON object body; the parameters are the
operation's, by name. A body that is not JSON, or not an object, is
`400`. An absent optional parameter and `null` are the same thing.
Every parameter is typed at the door, nested objects included: a string
where an integer belongs is `400` naming the parameter, and a JSON
boolean is never an integer. Keys the route does not know are ignored.
The optional `memory/search` v1 request instead rejects unknown fields and
mistyped values. Its defaults apply only when a field is absent; only its
`cursor` accepts `null`. This does not change legacy route decoders.
The turn-records routes are strict in the same way (Turn records version 1,
below). The `Content-Type` header is not read. A body over 64 MiB is `413`,
checked inside the bearer gate, so an unauthenticated oversize body is
still `401`; `conversation/write_records` takes at most 4,194,304 bytes.
A body whose declared length is over 64 MiB is refused before it is read,
with the message naming the ceiling of its route.

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
Three codes exist only on the wire: `value_error`, for a mis-typed or
missing parameter, for invalid store arguments (an empty message
list, a negative `after`) and for a request past an operation budget
(the message names the setting), `forbidden`, for the allowance,
and `storage_exceeded` (`413`), which no core backend answers: a gateway
or custom backend answers it when the destination refuses writes because
its owner's storage is full. Repeating does not help until space is freed
or the limit is raised; the binary's recorder stops landing to that
destination and tells the person once. A
malformed nested object (a message without `role`, an archive document
without `actor`) is `value_error` with the message
`malformed request: 'role'`. A `500` carries no envelope and is the
driver's problem, never a store answer.

## Spooled answers

An answer that can grow with stored content is written item by item into a
spool while the server reads the store: `memory/read`, `memory/versions`,
`memory/history`, `conversation/read_turns`, `conversation/read_projections`,
`conversation/search_turns` and `conversation/read_records`. An answer stays in memory while it is at most
1 MiB; past that the whole answer goes to a private temporary file and is
charged against `NEOSIAN_MAX_TEMP_BYTES` (512 MiB by default) for that one
answer. The server lets go of the store (the file store's lock, a Postgres
snapshot, transaction and connection) before it sends anything, then sends
the spool with its `Content-Length`. The bytes are exactly those of the whole
answer. A slow reader costs an open file and disk until it finishes; it never
holds the store, so other requests are not delayed.

`conversation/read_records` answers its turn headers before its records
although it reads each header with its turn's records, so it spools the two
lists apart and sends one after the other without copying either. The two
share the answer's 1 MiB of memory and its one temporary budget; at the
default page size neither reaches a file.

What the server holds while it reads: turn reads and searches hold about one
turn at a time, and `memory/read` the one document. `memory/versions` and
`memory/history` hold their page, up to 500 versions with their content (on
Postgres, 127 MiB for a page of 500 versions of 100 KB); the file store also
reads the document's whole journal, or for history every version of the scope,
on every page (320 MiB for one history page over 1,500 versions of 100 KB), as
2.16.0 did. `conversation/read_projections` holds its page of entries. A
listing asked for `limit: 0` reads no rows. The hosted `/mcp` door's
`continue_session` holds the whole conversation and its projections to build
a reply of about 64 KB (about the conversation's stored size: 142 MiB for
130 MB of turns).

The temporary directory (`TMPDIR` on Unix, `TMP` on Windows) must be writable and
disk-backed. A memory-backed one (tmpfs) counts against a container's memory
limit and defeats the bound. When no spool file can be made or written (an
unwritable directory, a full disk, a directory removed while in use), the
answer is finished in memory, as 2.16.0 held it, within
`NEOSIAN_MAX_RESPONSE_BYTES` (64 MiB by default), and refused past that with
`value_error` naming that setting; it is never a bare `500` or a cut body.
`neosian serve` checks the directory at startup and says once, on its log, when
it cannot spool there. Each server keeps its spools under its own locked
directory, `.neosian-serve-<uuid>`, removed when it stops. On Unix the
next startup removes an ended server's spools when the directory belongs to
the same user. On Windows the operator cleans up after a killed server.
Disk use is the answers being sent at once, each at most
`NEOSIAN_MAX_TEMP_BYTES`; nothing limits how many are sent at once except the
process's open-file limit (each costs a socket and a spool file), and no
timeout ends a stalled send.

Every refusal is known before the status line and is the ordinary response
with its status and envelope, wherever in the answer it was found. A request
whose answer is larger than `NEOSIAN_MAX_TEMP_BYTES` is refused with
`value_error` naming that setting; ask for less at a time instead: size pages
(`page_bytes`) for `conversation/search_turns` and `conversation/read_turns`,
a smaller `limit` for the memory listings, whose pages follow their cursor.
The binary does both by itself, keeping each page under 1 MiB, and reports the
first refusal for a second size refusal or a stale relevance continuation;
it never asks again after the candidate bound (`resource limit
NEOSIAN_SEARCH_MAX_CANDIDATES exceeded: …`), which no smaller answer cures.
A projection page cannot be made smaller on wire 6 (it ends on whole turns,
at 500 entries), so a projection page past the budget needs the setting
raised. One item larger than the budget has no smaller answer at all; neither
has one Postgres row whose compact JSON passes `NEOSIAN_MAX_RESPONSE_BYTES`,
the one check every Postgres read applies to each row. Requests are capped at
64 MiB, so through this wire neither can be written at the default settings
(see `neosian docs stores` for what bounds each record). A spool that cannot
be read back while sending ends the body short, and the connection closes, as
any I/O failure closes it. Database timeouts bound the store read:
`statement_timeout` each statement, `transaction_timeout` and
`idle_in_transaction_session_timeout` the read's transaction. Sending happens
after the connection is released, so no database timeout applies to it.

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
| `memory/changes` (optional v1) | `scope` req; `cursor`, `limit` (1..100, default 50) | Stream, events, checkpoint/head/floor cursors and explicit gap |
| `memory/prune_changes` (optional v1) | `scope`, `through` req; `write` (default false) | `format`, `scope`, `removed`, `write`, `earliest_cursor` |
| `memory/search` (optional v1) | `scope`, `query` req; `prefix`, `fields`, `metadata_keys`, `limit`, `cursor` | `hits`, `next_cursor` |

A write with no `actor` is recorded under the token's client
(`client:default`); with `actor: "me"` under `client:default/me`.
`redact` with no `path` redacts the whole scope and records a redaction
whose `path` is `null`. `versions` and `history` answer newest first.

### Memory search version 1

`POST /v1/memory/search` requires one explicit authorized `scope`. Token
allowances and exact-scope read grants are checked before scanning or paging;
an actor label, alternate scope array or cursor cannot widen that scope.
Query parameters and JSON schemas are in the OpenAPI artifact. For example:

```json
{"scope":"workspace:team","query":"deployment window","prefix":"notes/",
 "fields":["path","content"],"metadata_keys":[],"limit":20,"cursor":null}
```

The 1..16 whitespace terms (1..1024 characters total) must all match selected
values as substrings after per-scalar Unicode 17.0.0 lowercase. White_Space
uses that Unicode version too. No normalization, accent removal, stemming or
regex is applied. `path` and `content` are the default fields. Selecting
`metadata` requires 1..8 explicit top-level `extra` keys (1..128 characters
each); only their recursive string values participate, never object keys or
numeric/boolean/null values. Unselected metadata is not returned. A prefix
is a case-sensitive literal document prefix, at most 1024 UTF-8 bytes.

Hits contain `scope`, `path`, `version`, UTC `created_at`/`updated_at`, `actor`,
`matched_fields` and one `excerpt`. The excerpt has `field`, nullable
`metadata_key`, a zero-based Unicode-character `offset` within the selected
string, and `text` of at most 240 characters. It starts up to 40 characters
before the first match, preferring content, then selected metadata, then path.
Metadata objects traverse keys in ascending order; arrays retain their order.
Fetch the current authoritative document for its full meaning and version.

Paths sort ascending with bytewise ASCII ordering. Limit defaults to 20 and
must be 1..100. A nonfinal page has exactly `limit` hits and an opaque
`next_cursor` (1..4096 bytes); the final page has `null`. Bind cursors to the
 scope, query, prefix, fields and metadata keys; reuse with a different target
or query is `value_error`. Limit may change. Cursors carry no read authority.
There is no total count or cross-page snapshot. Each page reads current data;
new/renamed paths before the last key require restarting the query.

Only current, non-redacted documents participate. Old versions, deleted
documents, `sessions/`, `handoff`, `messages/` and `message-links/` are excluded
even when a path or retained metadata matches. Reference file search holds
the root lock and Postgres uses one statement snapshot; neither creates a
search index or cache. A completed erasure therefore affects the next query
even though it may preserve version, timestamps and metadata. Output limits
do not bound scan work. `neosian docs memory` describes the mounted CLI/MCP
forms, which add source references and preserve backend cursor opacity.

With knowledge-policy capability version 1, `knowledge_policy` accepts `all`
(omitted default), `effective` or `approved`. Filter current governance state
before matching, excerpts and lookahead, at one server UTC instant per scan.
Effective includes unmanaged documents and effective approved content; approved
requires valid content-bound approval. Draft, superseded, expired, scheduled,
stale and invalid governed content is excluded. Cursors also bind this policy.
Scope grants still authorize every page; policy is a selection, not access
authority. Raw reads/history remain unchanged. `neosian docs governance` and
`schema governance` specify metadata, approval hashes and time boundaries.

### Memory ranking version 1

With `memory_ranking` version 1, `memory/search` accepts three more optional
fields. A request without them, or with them null, is a version 1 request and
its response is unchanged.

- `order`: `path`, `relevance` or `newest`. A response to a request that names
  an order echoes it as `order`. `path` is version 1 ordering and paging.
  `relevance` and `newest` return at most `limit` hits with a null
  `next_cursor`; a cursor with either is `value_error`.
- `updated_since` (inclusive) and `updated_before` (exclusive): UTC RFC 3339
  instants ending in `Z`, compared with the current version's `updated_at`.
  Since must precede before. Bounds narrow every order and, like an explicit
  order, bind into path cursors.

Relevance candidates are eligible documents holding at least one term in a
selected field; path and newest keep the all-terms rule. Newest sorts by
`updated_at` descending, then path ascending. Relevance computes its
statistics over the eligible selection of the one requested scope: the
prefix, searchable paths, non-redacted current documents, the knowledge
policy and the date bounds all apply first. For each document, `tf` counts
non-overlapping left-to-right occurrences of a term summed over the selected
values after version 1 lowercasing, and `length` counts the Unicode scalars
of those lowercased values. `N` is the number of eligible documents, `df` the
number holding the term, `avgdl` the total length divided by `N`. Then

```text
idf   = ln(1 + (N - df + 0.5) / (df + 0.5))
score = sum, over distinct terms in ascending codepoint order with tf > 0, of
        idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avgdl))
k1 = 1.2, b = 0.75
```

Each relevance hit carries `score`, the round-half-even integer of score times
one million. Hits sort by score descending, then path ascending. Clients
validate the echoed order, the sort order, score presence, bounds and the null
cursor. Scores belong to one scope: a client merging scopes merges by rank,
never by comparing scores. Ranking grants no access, and its statistics never
include documents outside the authorized scope.

### Memory passages version 1

With `memory_passages` version 1, `memory/search` accepts an optional integer
`passages`, 0 to 3. A request with 1 or more gives every hit a `passages`
array, possibly empty; absent, null or 0 keeps version 1 hits. Passages never
change matching, order, scores or cursors and are not bound into cursors.

Passages come from selected `content` and metadata string values longer than
240 Unicode scalars; paths never yield passages. Each non-overlapping
occurrence of a term, found after version 1 lowercasing and mapped back to
original scalars, proposes a window starting 40 scalars before it (not before
0) and spanning at most 240 scalars. A window's key is the number of distinct
terms, then of occurrences, lying wholly inside it. Windows are chosen by key
descending, then value order (content, then metadata in version 1 order),
then offset ascending, skipping any that overlap a chosen window of the same
value, up to the requested count, and are listed in value order, then offset.

Each passage has `field`, `metadata_key`, a zero-based scalar `offset` in its
value, `text` (1 to 240 scalars) and `heading`. For content, `heading` is the
nearest ATX heading whose line starts at or before the window: up to three
leading spaces, one to six `#`, then a space, tab or line end. Lines inside a
fence (three or more backticks or tildes, closed by a run of the same
character at least as long), a leading `---` frontmatter block and a leading
`<!--` comment block are not headings. The text drops the markers, a closing
`#` run that follows a space, and surrounding spaces, truncated to 240
scalars; an empty heading is null. Metadata passages have a null heading.
Clients validate presence, count, fields, keys, lengths and content order.

### Conversation

| route | request | response |
|---|---|---|
| `conversation/append_turn` | `conversation_id`, `messages` req; `actor` | `turn` |
| `conversation/read_turns` | `conversation_id` req; `after`, `limit`; `page_bytes`, `continuation` with size paging | `turns`, `next_after`; `continuation` with size paging |
| `conversation/last_turn_number` | `conversation_id` req | `turn` (int, 0 when none) |
| `conversation/search_turns` | `query` req; `conversations` (a list of ids), `limit` (default 50, at most 500); `order`, `since`, `before` with history ranking; `page_bytes`, `continuation` with size paging | `turns`; `order`, `scores` with history ranking; `continuation` with size paging; `omitted` where turns are stored as records |
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
`read_turns` shape. Where a store keeps turns written in parts as records
(`neosian docs stores`), such a turn is matched and answered as its
messages stored whole; one longer than the server's
`NEOSIAN_MAX_TURN_BYTES` keeps its place among the winners, counts in the
statistics that score the others, and is not answered: the answer adds
`omitted`, how many were left out, as its last key (absent when none). A
size page passes such turns without counting them toward `page_bytes`,
never ends with them alone while another page follows, and resumes after
the last turn it passed, so each is counted once. History ranking version 1, below, adds orders and
turn-time bounds; a request without its fields answers exactly this.

`redact_turns` and `turn_redactions` (`WIRE_VERSION` 6) are the eraser
(`neosian docs stores`). `redact_turns` blanks the selected turns of one
conversation and every projection entry covering them, keeps each
turn's number, `created_at` and `actor`, and answers the count matched,
including already-redacted turns. Each call that matches at least one
turn adds one trail entry, including a repeated call.

Omit both `through` and `turns` to erase every turn. On a store with
records enabled this also removes the conversation's pending drafts,
staged restores and committed restore receipts, even when the count is
zero. A `through` bound or an explicit `turns` list preserves that work,
even if it selects every visible turn. An empty list, an unknown
conversation, or a list containing only unknown turns matches zero turns.
The bound is inclusive and turn numbers start at 1. Giving both selectors
or a number below 1 returns `value_error`.

The act is recorded under the token's client like a write (`actor: "ops"`
records `client:default/ops`). `turn_redactions` answers the trail
newest first and is bounded like a search: `limit` above 500 is
`value_error`, `conversations` narrows as it does for a search, and a
redacted turn never answers a search again. A backend whose `erasable`
is `false` answers both routes with `agent_configuration_error`.

### History ranking version 1

With `history_ranking` version 1, `conversation/search_turns` accepts three
more optional fields. A request without them, or with them null, is a version
1 request and its response is unchanged.

- `order`: `newest` or `relevance`. A response to a request that names an
  order echoes it as `order`; without one the order is newest and nothing is
  echoed. Neither order pages, and `limit` keeps its bounds.
- `since` (inclusive) and `before` (exclusive): UTC RFC 3339 instants ending
  in `Z`, compared with each turn's `created_at`. Since must precede before.
  Bounds narrow both orders. Turns are immutable, so `before` is an exact
  as-of cutoff.

Newest is the version 1 answer inside the bounds: the same matching, total
order and limit. Relevance candidates are the turns of the selected
conversations, inside the bounds, whose searchable text (the text above)
holds at least one term under the backend's version 1 matching. Statistics
come from the candidates, not every stored turn: `N` is the number of
candidates, `df` the number holding the term, `avgdl` their total length
divided by `N`. `tf` counts non-overlapping left-to-right occurrences of a
term in the searchable text after Unicode lowercase, and `length` counts the
Unicode scalars of that folded text. Files match with Unicode lowercase too.
Postgres matches with `ILIKE` under the database's collation, exactly as
version 1 does, and counts over the searchable text it stored; a candidate
whose counts are all zero there still counts toward `N` and `avgdl`.
Backends agree wherever the database's case folding agrees with Unicode
lowercase, which includes all ASCII text. The formula, `k1`, `b` and the
round-half-even quantization are memory ranking version 1's. A relevance
response adds `scores`, integers parallel to `turns` and never increasing;
ties follow the version 1 total order (`created_at`, then conversation id,
then turn number, each descending). Redacted turns hold no text and never
count. An unknown order, a malformed or non-UTC bound, or a since that does
not precede before is `value_error`. Clients validate the echoed order, the
scores and their order, the bounds, the limit and the selected conversations.
Ranking grants no access: a constrained token still names its conversations.
A server may bound how many candidates one relevance request considers; the
reference server's bound is `NEOSIAN_SEARCH_MAX_CANDIDATES`. Past it the
answer is `value_error` whose message starts `resource limit
NEOSIAN_SEARCH_MAX_CANDIDATES exceeded` and asks the caller to narrow the
words or the time range. Memory relevance follows the same rule. Newest
answers are not limited this way.

### Size paging version 1

With `size_paging` version 1, `conversation/search_turns` (version 1, newest
and relevance) and `conversation/read_turns` accept two more optional fields.
A request without them, or with them null, is unchanged, byte for byte.

- `page_bytes`: an integer of at least 1, the page's byte ceiling. A request
  carrying it is size-paged, and its answer adds `continuation`: a string when
  more turns remain, else `null`. The server serves a larger ceiling as its
  own cap, `NEOSIAN_MAX_PAGE_BYTES` on the reference server (8 MiB by default).
- `continuation`: the previous page's continuation, sent back with the same
  request; it requires `page_bytes`, which may change between pages.

A page holds whole turns in answer order while their encodings together stay
within `page_bytes`, counting each turn as the UTF-8 length of its compact
JSON, and always at least one turn, so a turn larger than the ceiling is a page
of its own. A page ends only before a turn that would pass the ceiling, at the
end of the answer, at `limit`, or at 500 turns for `read_turns`. `limit` keeps
its meaning and bounds the total across pages; without one `read_turns`
continues to the end of the conversation. A size-paged `read_turns` answer
carries `next_after: null`: `continuation` alone continues.

A continuation is opaque, at most 4096 bytes. It binds the request's
selection (the conversation, `after` and `limit` of a read; the query terms,
`conversations`, `order`, `since`, `before` and `limit` of a search) and a
position; sent with a different selection it is `value_error`. It names a
place, never a permission: every page is authorized from its own body, and a
constrained token still names its conversations.

Version 1, newest and `read_turns` continue after the last turn returned, and
each page reads current state, as memory cursors do. Relevance has no cursor
without size paging, because its scores depend on every candidate. Its pages
count over exactly the selection an unpaged request counts over, so the first
page is the start of the unpaged answer; later pages record the candidates'
statistics and continue after the last turn returned. Turns never change, so
the scores and order are the same; when the candidates did change (a new
turn, an erasure, a restore), the page refuses with `value_error`: `page
continuation is stale: the candidate turns changed since the first page;
search again`. When the binary pages only to answer a refused request and a
page refuses as stale, it shows the first refusal (the answer was too large
for the server's budget); the same search succeeds once writes to those
conversations pause.

Each search page repeats the search: newest and version 1 scan every
candidate again (schema 4 has no index on turn time), and relevance counts
again. A limit-500 newest search over 500 turns of 64 KiB took 0.7 s unpaged
and about 22 s in 1 MiB pages on one Postgres server. Page a search only to
bound what one response carries, with as large a ceiling as you can hold. A
`read_turns` page continues by turn number and costs little more than its
rows: on files about the same as an unpaged read; on Postgres, at 8 MiB
pages, 2 to 14% more for the same turns (more at smaller pages), because each
page reads one row past its end to know whether more follow.

`memory/read`, `memory/versions`, `memory/history` and
`conversation/read_projections` are spooled but not size-paged in version 1: a
document is one item, memory listings already take a row `limit` and a
cursor, and a projection page already ends on a whole turn.

### Turn records version 1

Turn records version 1 stores and moves a turn as bounded records, so no
request or answer has to hold a long turn whole. Its routes answer on a
store that keeps records (`neosian docs stores`): a FileStore after
`neosian records enable`, read on every request, so a running server serves
them without a restart, and Postgres with the records extension installed
before the server started. On any other store each of them answers the
store's own `400 agent_configuration_error` to every body that is a JSON
object, before any of its fields is read; a body that is not JSON or not an
object is `400 value_error` first, as on every route, and the bearer gate
and the token's allowance come before both.
`append_turn` and every other route keep their bytes on both kinds of store.

| route | request | response |
|---|---|---|
| `conversation/write_records` | `conversation_id`, `draft`, `records` req; `turn_bytes`, `seal`, `discard`, `actor` | `draft`, `state`, `next_seq`, `turn`, `stored_bytes`, `freed_bytes` |
| `conversation/read_records` | `conversation_id` req; `turn`, `after`, `through`, `seq`, `piece`, `kinds`, `heads`, `page_bytes`, `continuation` | `turns`, `records`, `continuation` |
| `conversation/search_records` | `query` req; `conversations`, `kinds`, `order`, `since`, `before`, `limit` | `hits`; `order` when the request named one |
| `store/restore_records` | `conversation_id`, `restore` req; `turns`, `records`, `projections_from`, `projections`, `redactions_from`, `redactions`, `commit`, `discard` | `restore`, `state`, `position`, `stored_bytes`, `freed_bytes`, `digest` |
| `store/sweep_records` | `limit`, `cursor`, `write` | `conversations`, `next_cursor`, `write` |

**Shared rules.** These routes differ from the legacy ones in four ways.

- **Strict bodies.** An unknown field and a mistyped value are
  `400 value_error`, in the request and in each record, and in each turn
  header, projection and trail act of a `restore_records` body. A default
  applies only when a field is absent, and `null` is accepted only where a
  table below says "or null".
- **Fixed answer key order.** An answer writes its keys in the order its
  table lists them, and so do a turn header, an answered record and a hit,
  so a verifier can stream.
- **Body ceiling.** A `write_records` body and a `restore_records` body
  are at most 4,194,304 bytes and hold at most 2,048 items: the records of
  a write, or the headers, records, projections and trail acts of a restore
  part counted together. A longer body is `413 value_error` with the
  message `request body exceeds 4194304 bytes`, refused while it is read and
  before it is parsed, inside the bearer gate; more items are
  `400 value_error`. `read_records`, `search_records` and `sweep_records`
  keep the general 64 MiB ceiling.
- **Authorization.** `write_records` needs the conversation inside the
  token's allowance and, with grants, `append` on it; `read_records` needs
  the same allowance and, with grants, `read`. Both are checked from the
  body's `conversation_id` before the store is asked anything and before
  the other fields are decoded, so a refused client receives the same
  `403` for a conversation that exists, one that does not and a request
  that would have been refused for another reason. `search_records` needs
  the same of every conversation in its `conversations`, checked from that
  list before anything is scanned: a constrained token must send the list
  (no other field stands in for it), and with grants every client must,
  the operator included. An empty list reads nothing and answers no hits
  to a client its allowance and its grants admit: any token without
  grants, and with grants any client that has a grant entry. A client with
  no grant entry is refused, also for `[]`.
  `store/restore_records` and `store/sweep_records` are whole-store
  routes: a constrained token is refused whatever its body names, the
  conversation inside its allowance included, and with grants only an
  `operator` client is allowed.

A conflict is `400 agent_conversation_conflict` with a `reason` in `details`
beside `conversation_id`:

| `reason` | other `details` |
|---|---|
| `draft_gap` | `next_seq` |
| `draft_mismatch` | `next_seq`, `seq` (the first differing record) |
| `draft_actor` | `draft` |
| `draft_size` | `turn_bytes`, `bytes` (see below) |
| `draft_sealed` | `turn` (the sealed turn's header) |
| `drafts_open` | `limit` (16) |
| `target_occupied` | none |
| `restore_open` | `restore` (the open restore's id) |
| `restore_gap`, `restore_mismatch`, `restore_incomplete`, `restore_committed` | `position` (see `restore_records`) |

The `details` keys are answered in this order: `conversation_id`, `reason`,
then the others as listed. `draft_size` has three cases:

- The request names no total, or the draft's own, and its records would
  take the draft past the pinned total, or its seal finds other bytes than
  the pinned total: `turn_bytes` is the pinned total and `bytes` is what the
  draft would hold with the request's records.
- The request names a total other than the one pinned: `turn_bytes` is the
  request's value and `bytes` is what the draft holds now, without the
  request's records.
- The request names a total on a draft opened without one: `turn_bytes` is
  the request's value and `bytes` is what the draft holds now.

A sealed draft keeps no pinned total: a later request naming a total other
than the header's `bytes` is `draft_size` with the request's value and the
header's `bytes`.

A turn larger than the store keeps is refused as an operation budget:
`413 value_error` with the message `resource limit
NEOSIAN_MAX_RECORD_TURN_BYTES exceeded: <bytes> > <maximum>; this store does
not keep a turn this large`. The two `413 value_error` messages differ from
their first word, so a client tells a body that is too long from a turn the
store will not keep without reading further. A gateway or custom backend may
also answer `413 storage_exceeded` (see the envelope).

**A record** is one bounded piece of one message of one turn. A written
record has these keys; a key that may be null may be omitted, and key order
is free in a request.

| key | type | meaning |
|---|---|---|
| `seq` | integer >= 0 | its place in the turn, dense from 0 |
| `message` | integer >= 0 | its message's index in the turn, dense from 0 |
| `role` | `user`, `assistant`, `system`, `tool` | its message's role |
| `kind` | one of the eight kinds | what it holds |
| `content` | `text`, `blocks`, `none` or null | the message's content form; non-null only on a message's first record |
| `block` | integer >= 0 or null | the content block's index, for a record holding one block |
| `call` | integer >= 0 or null | the tool call's index in its message, on `tool_call` records and on the `extra` records of a call |
| `call_id` | string or null | the call's id on `tool_call`; the message's `tool_call_id` on every `tool_result` record and on every record of the message's first piece |
| `name` | string or null | the tool's name, on `tool_call` only |
| `chunk` | integer >= 0 | which chunk of its piece, from 0 |
| `size` | integer >= 0 | UTF-8 bytes of the whole piece, the same on every chunk |
| `text` | string | this chunk's text |

| kind | holds | text is |
|---|---|---|
| `prompt` | content of a `user` message | the text |
| `reply` | content of an `assistant` message | the text |
| `system` | content of a `system` message | the text |
| `tool_result` | content of a `tool` message | the text |
| `tool_call` | one tool call | compact JSON of its `arguments` |
| `reasoning` | a message's non-null `reasoning` | compact JSON of the value |
| `attachment` | one non-text content block | compact JSON of the block |
| `extra` | a message's or a call's non-empty `extra` | compact JSON of the value |

The records of one message, in order: its content (`text`: the string as
records of the role's kind; `blocks`: each block in order, a text block as
records of the role's kind with `block` set, any other block as `attachment`
records with `block` set; `none`: nothing); each tool call in order, its
`tool_call` records, then `extra` records with the same `call`; `reasoning`
records; `extra` records with `call: null`. The first record carries
`content`. A message that would produce no record produces one empty record
of its role's kind (`text: ""`, `size: 0`), and so does a message with a
`tool_call_id` whose first piece would be a `tool_call`. JSON texts are the
message codec's own compact form, so a turn rebuilt from its records is the
same messages `read_turns` answers for a turn stored whole.

The bound of a record is on `text` as the wire encodes it: its compact JSON
string, non-ASCII raw, quotes included, is at most 65,536 bytes. `call_id`
and `name` are at most 1,024 UTF-8 bytes each, so one record is at most
81,920 bytes. A longer piece is sent as chunks numbered from 0, every chunk
non-empty unless the piece is empty, their lengths adding up to `size`. The
server accepts any split within these bounds. The binary cuts the longest
prefix that fits, then back to just after the last line feed in its final
4,096 bytes, else after the last white-space character there, else where it
is.

**An answered record** (`read_records`) has all fifteen keys, in this
order: `turn` (its turn's number), the twelve keys above in their order,
then `tail` (string or null) and `truncated` (boolean). `tail` is null and
`truncated` false except on a head (see `read_records`). It is at most
81,920 bytes too.

**A turn header** has these keys, in this order: `turn` (integer >= 1),
`created_at` (the store's time at the seal, or the archive's for a
restored turn), `actor` (string or null),
`redacted` (boolean), `records` (integer >= 0) and `bytes` (integer >= 0,
the sum of `size` over the turn's pieces). A turn stored whole is answered
with the header its records would have, and a redacted turn with its
number, time and actor, `redacted: true` and no records. A header is at
most `192 + 6 * u(actor)` bytes.

**The size rule.** `stored_bytes` and `freed_bytes` count logical bytes by
one rule on every backend, so a host can reserve from the decoded request.
`u(x)` is the UTF-8 length of `x`, 0 for null or absent; `c` is
`u(conversation_id)` and `o` is `u(draft)` or `u(restore)`.

```text
record           = u(text) + u(call_id) + u(name) + 2c + 256
turn header      = u(actor) + o + 4c + 512     at the seal, with the stamped actor; a restored turn not redacted
redacted header  = u(actor) + 2c + 256         a restored redacted turn
projection       = u(text) + 2c + 128          a restored projection
trail act        = u(actor) + 4 * len(turns) + 2c + 128   a restored trail act
bookkeeping      = o + 2c + 1024               the first request of a draft or restore; freed at seal, commit or discard
conversation row = c + 256                     a seal or commit that creates the conversation
committed marker = o + 2c + 256                at commit; kept until the conversation is erased

stored(write_records)   = sum(record) + [first request: bookkeeping]
                          + [seal: turn header (+ conversation row)]
stored(restore_records) = sum(record) + sum(headers) + sum(projection) + sum(trail act)
                          + [first part: bookkeeping]
                          + [commit: committed marker (+ conversation row)]
```

A restore counts its headers with the actors it sends. A repeat adds zero. A `write_records` answer is at most
`2,048 + 6 * u(actor)` bytes, `actor` being the draft's stamped actor: no
length bounds an actor, and a control character in it is answered as six
bytes. An answer without a turn header is within 2,048 bytes. The same bound
covers a `draft_sealed` refusal, whose details carry the header. A
`restore_records` answer is at most 2,048 bytes: it carries no actor and no
header, only the restore's id, five counts, two byte counts and the digest
(at most 398 bytes with the longest id and every number at twenty
digits).

A `read_records` answer is at most
`max(min(page_bytes, NEOSIAN_MAX_PAGE_BYTES), 81,920 + h) + 4,160` bytes,
`h` being the first header of the page: a page past its size holds exactly
one record and its turn's header, and the 4,160 bytes cover the three keys
and a continuation, which is at most 4,096 bytes (today at most 256). At
the default `page_bytes` that is 987,200 bytes, under 1 MiB.

A `search_records` answer is at most `limit * (16,384 + 6 * u(actor)) + 64`
bytes, `actor` being the longest actor among the turns hit: a hit is at
most 16,384 bytes beside its actor with the longest ids and every character
escaped, and is typically about 600. At the default `limit` and with actors
of at most 256 bytes that is 358,464 bytes. A `sweep_records` answer is at
most `limit * 2,048 + 4,160` bytes. These are bounds of `200` answers, not
of refusals. A refusal of a turn-records route repeats at most the first 128
scalars of these values of the request: an unknown field's name, in the
body or in a record, header, projection or trail act; an unknown kind,
role or content form; a draft id or a restore id; a projection's kind; a
trail act's conversation; a time that is refused. Those do not grow with
the request. Two values are repeated whole: a malformed sweep cursor, up
to its 4,096 bytes, and a conversation id outside the id grammar, in the
frozen `agent_conversation_id_invalid` envelope.

**`conversation/write_records`** writes one turn in parts. A draft is one
turn being written; the client names it; nothing of it is visible until a
request seals it, then the turn appears whole.

| request field | type | rule |
|---|---|---|
| `conversation_id` | string, required | the id grammar |
| `draft` | string, required | 1 to 64 of `[a-z0-9_-]`, chosen by the client, unique within the conversation |
| `records` | list, required | 0 to 2,048 written records with consecutive `seq` |
| `turn_bytes` | integer >= 0 | the `bytes` the sealed turn's header will carry; on the opening request, or repeated unchanged later |
| `seal` | boolean, default false | after storing these records, make the turn visible |
| `discard` | boolean, default false | drop the draft and its records; `records` empty, `seal` false |
| `actor` | string or null | stamped under the token's client as `append_turn` stamps it; later requests repeat it or omit it |

| response field, in this order | type | meaning |
|---|---|---|
| `draft` | string | echoed |
| `state` | `none`, `open`, `sealed`, `discarded` | `none`: no such draft |
| `next_seq` | integer | records stored so far |
| `turn` | header or null | the turn header once sealed |
| `stored_bytes` | integer | size-rule bytes this request added, 0 for a pure repeat |
| `freed_bytes` | integer | size-rule bytes this request removed |

- **Probe.** `records: []` without `seal` or `discard` creates nothing and
  answers the draft's state: `none`, `open` with `next_seq`, or `sealed`
  with its turn. A client resumes from it after an interruption. A probe
  is not a touch: a draft's age runs from the last request that carried
  records, a pure repeat included.
- **Opening.** The first request with records opens the draft. Once its own
  checks have passed, the server discards that conversation's drafts
  untouched for more than `NEOSIAN_DRAFT_SECONDS` and reports their bytes in
  `freed_bytes`. A conversation holds sixteen open drafts; one more is
  `drafts_open`.
- **The stated total.** A `turn_bytes` above
  `NEOSIAN_MAX_RECORD_TURN_BYTES` is refused at the opening with the `413`
  above, nothing stored. Once stated it is pinned: a record that would take
  the draft past it, a seal whose bytes differ from it, a later request
  naming another value, and a later request naming one on a draft opened
  without it are `draft_size`; nothing of that request is stored and the
  draft stays open.
- **The store's bound.** Stated or not, a record that would take the draft
  past `NEOSIAN_MAX_RECORD_TURN_BYTES`, and a seal of a draft past it, are
  the same `413`.
- **Actor.** The first request fixes the draft's actor: `actor` stamped
  under the token's client, or the client alone when the request names none.
  A later request may repeat it or omit it; one naming a different actor is
  `draft_actor`.
- **Discard.** A discard is checked like any later request: naming a
  different actor is `draft_actor`, naming another `turn_bytes`, or one on a
  draft opened without, is `draft_size`, and nothing is discarded. The
  answer's `freed_bytes` is everything the draft held.
- **Repeats.** Records with `seq` below `next_seq` must equal the stored
  ones and change nothing (`draft_mismatch` otherwise); records from
  `next_seq` on are appended; a first new `seq` above `next_seq` is
  `draft_gap`. A part repeated after a lost answer is harmless, even under
  other part boundaries.
- **Checks.** Each record alone (its bounds, its kind against its role, its
  chunk against its size) and against the draft (message indexes dense, a
  message's first record carrying `content`, a piece's chunks consecutive).
  A JSON piece must be exactly the compact JSON the message codec writes
  for its value. A refused request stores nothing.
- **Seal.** A seal needs at least one record and a complete last piece. It
  assigns the next turn number and the time together. Sealing again with
  the same or no records answers the same header; new records after a seal,
  and a discard, are `draft_sealed`.
- **Nothing of an open draft is visible.** It appears in no `read_turns`,
  search, `last_turn_number` or `store/conversations` answer.

A prompt, one `Bash` call, a tool result of 812,345 bytes in thirteen
chunks of at most 65,000 bytes and a reply, written in two parts (texts
shortened):

```json
POST /v1/conversation/write_records
{"conversation_id":"cc-7f3a","draft":"d7c1e0b2a9f44e57","actor":"claude-code:7f3a","turn_bytes":812400,
 "records":[
  {"seq":0,"message":0,"role":"user","kind":"prompt","content":"text","chunk":0,"size":13,"text":"run the tests"},
  {"seq":1,"message":1,"role":"assistant","kind":"tool_call","content":"none","call":0,
   "call_id":"toolu_01","name":"Bash","chunk":0,"size":23,"text":"{\"command\":\"make test\"}"},
  {"seq":2,"message":2,"role":"tool","kind":"tool_result","content":"text","call_id":"toolu_01",
   "chunk":0,"size":812345,"text":"..."},
  ... seq 3 to 8, chunks 1 to 6 ...]}

200 {"draft":"d7c1e0b2a9f44e57","state":"open","next_seq":9,"turn":null,
     "stored_bytes":458588,"freed_bytes":0}

POST /v1/conversation/write_records
{"conversation_id":"cc-7f3a","draft":"d7c1e0b2a9f44e57","actor":"claude-code:7f3a","seal":true,
 "records":[ ... seq 9 to 14, chunks 7 to 12 ...,
  {"seq":15,"message":3,"role":"assistant","kind":"reply","content":"text","chunk":0,"size":19,
   "text":"All 412 tests pass."}]}

200 {"draft":"d7c1e0b2a9f44e57","state":"sealed","next_seq":16,
     "turn":{"turn":42,"created_at":"2026-11-21T14:03:07.512000Z",
             "actor":"client:default/claude-code:7f3a","redacted":false,
             "records":16,"bytes":812400},
     "stored_bytes":359889,"freed_bytes":1054}
```

The first part's `stored_bytes` includes the draft's bookkeeping, which the
seal frees.

**`conversation/read_records`** reads a conversation's turns as records,
under a view, a page at a time. It reads every turn, however it is stored:
a turn stored whole is converted to its records as the read reaches it, so
the same messages answer the same records either way.

| request field | type | rule |
|---|---|---|
| `conversation_id` | string, required | the id grammar |
| `turn` | integer >= 1 | this turn only; excludes `after` and `through` |
| `after` | integer >= 0, default 0 | turns after this number |
| `through` | integer >= 1 | turns up to this number, inclusive |
| `seq` | integer >= 0 | start at this record of the turn; requires `turn` |
| `piece` | boolean, default false | only the piece holding record `seq`, all its chunks; requires `turn` and `seq` |
| `kinds` | list of kinds | the kinds answered; all eight when absent; `[]` answers headers only |
| `heads` | object, kind to integer 1 to 32,768 | pieces of these kinds are answered as a head and a tail |
| `page_bytes` | integer >= 1, default 983,040 | the page's size; served at most `NEOSIAN_MAX_PAGE_BYTES` |
| `continuation` | string, 1 to 4,096 bytes | the previous page's |

No field accepts `null`, and a field that is named counts even at its
default: `after: 0` beside `turn` and `piece: false` without `turn` and
`seq` are `value_error`, as `after: 1` and `piece: true` would be.

| response field, in this order | type | meaning |
|---|---|---|
| `turns` | list of turn headers | every turn the page reaches, in order, with records on the page or passed with none selected |
| `records` | list of answered records | in (`turn`, `seq`) order, of the kinds asked |
| `continuation` | string or null | null on the last page |

A conversation that does not exist, a `turn` that does not, and a range
holding no turn answer `{"turns":[],"records":[],"continuation":null}`.
A `seq` past the turn's last record answers the turn's header and no
record.

- **Views.** A view is a kind selection: prompts and replies
  `["prompt","reply"]`; prompts alone `["prompt"]`; tool calls without
  their results `["tool_call"]`, or with `"prompt"` and `"reply"` for
  context; everything, `kinds` omitted. `kinds` is a set: order and repeats
  do not matter, though every entry must be a kind. Every header in range is answered whatever the view, so a reader
  sees a turn exists though none of its records is selected.
- **Heads.** `heads: {"tool_result": 4096}` answers each `tool_result`
  piece whose text encodes to more than twice 4,096 bytes as one record:
  `chunk: 0`, `text` the longest prefix whose JSON string encoding, quotes
  included, is at most 4,096 bytes, `tail` the longest suffix within the
  same bound, `size` the whole piece's bytes, `truncated: true`, the other
  keys those of the piece's first chunk. Its other chunks are not answered.
  A piece that encodes to at most twice the bytes is answered whole, each
  chunk with `tail: null` and `truncated: false`. A head applies to a piece
  whose first chunk is at or after the record the read starts at; a read
  that starts inside a piece answers its remaining chunks as they are.
- **A piece whole.** `turn`, the `seq` of any chunk of a piece and
  `piece: true` answer that piece's chunks and nothing else, paged like any
  read. `heads` is ignored; `kinds` still applies. With `kinds: []` no
  record is read at all, only headers.
- **Pages by bytes.** A header or a record costs its compact encoding plus
  one byte. A turn's header is taken together with that turn's first record
  on the page, as one group; each further record is a group, and so is the
  header of a turn with no record selected. A page takes groups in order
  while their costs together stay within `page_bytes`, and always at least
  one, so it ends only between records and a turn of any size reads in
  pages of the size asked. A page that ends inside a turn is followed by
  one that repeats that turn's header.
- **Continuation.** Opaque. It binds the selection (`conversation_id`,
  `turn`, `after`, `through`, `seq`, `piece`, `kinds` as a set and `heads`)
  and where the next page starts; sent with another selection it is
  `value_error` with the message `page continuation belongs to a different
  request`. `page_bytes` may differ from page to page. It names a place,
  never a permission: every page is authorized from its own body, and a
  continuation whose place lies outside what the request itself selects
  (another turn than `turn`, a turn outside `after` and `through`, a record
  before `seq`, a record outside the piece fetched) is refused with the
  same message, so it reads nothing the request alone could not. Turns do
  not change, so a later page can only add turns sealed since.
- **A turn that cannot be records.** A turn stored whole whose tool-call id
  or tool name is longer than 1,024 bytes is refused with
  `400 agent_conversation_format_unsupported` naming the turn and the
  bound, by any read whose range reaches it; `read_turns` still answers it
  whole.
- **What is never read.** An open draft, a restore that is not committed,
  and the records of a redacted turn.

The light view of the turn written above, with tool heads, then that
result whole:

```json
POST /v1/conversation/read_records
{"conversation_id":"cc-7f3a","turn":42,
 "kinds":["prompt","reply","tool_call","tool_result"],"heads":{"tool_result":200}}

200 {"turns":[{"turn":42,"created_at":"2026-11-21T14:03:07.512000Z",
               "actor":"client:default/claude-code:7f3a","redacted":false,"records":16,"bytes":812400}],
     "records":[
      {"turn":42,"seq":0,"message":0,"role":"user","kind":"prompt","content":"text","block":null,"call":null,
       "call_id":null,"name":null,"chunk":0,"size":13,"text":"run the tests","tail":null,"truncated":false},
      {"turn":42,"seq":1, ... ,"kind":"tool_call", ... ,"text":"{\"command\":\"make test\"}","tail":null,"truncated":false},
      {"turn":42,"seq":2,"message":2,"role":"tool","kind":"tool_result","content":"text","block":null,"call":null,
       "call_id":"toolu_01","name":null,"chunk":0,"size":812345,"text":"cargo test --locked ...",
       "tail":"... test result: ok. 412 passed; 0 failed","truncated":true},
      {"turn":42,"seq":15, ... ,"kind":"reply", ... ,"text":"All 412 tests pass.","tail":null,"truncated":false}],
     "continuation":null}

POST /v1/conversation/read_records
{"conversation_id":"cc-7f3a","turn":42,"seq":2,"piece":true,"page_bytes":262144}

200 {"turns":[{...turn 42...}],"records":[{...seq 2...},{...seq 3...},{...seq 4...}],"continuation":"WyJyZWNvcmRzLXYxL3JlYWQi..."}
```

The continuation is shortened here with `...`.

**`conversation/search_records`** searches records, the unit `read_records`
answers, over every searchable kind. A hit names one record; read it with
`read_records` (`turn`, `seq`, `piece: true`) or read around it with `turn`.
Matching a query whose terms are spread over several records of a turn
stays with `conversation/search_turns`.

| request field | type | rule |
|---|---|---|
| `query` | string, required | 1 to 16 whitespace terms in 1 to 1,024 characters, memory search's bounds; a term repeated counts once |
| `conversations` | list of ids | the conversations searched, as a set; absent: every conversation, for a token with no allowance and without grants only; `[]` answers no hits |
| `kinds` | list of searchable kinds | a set; absent: all five (`prompt`, `reply`, `system`, `tool_result`, `tool_call`); `[]` answers no hits; `reasoning`, `attachment` and `extra` are `value_error` |
| `order` | `newest` or `relevance`, default `newest` | echoed when given, also as `newest` |
| `since`, `before` | RFC 3339 instants ending in `Z` | compared with the turn's `created_at`: `since` inclusive, `before` exclusive, `since` before `before` |
| `limit` | integer 1 to 100, default 20 | the most hits answered; this route does not page |

| response field (in this order) | type | meaning |
|---|---|---|
| `hits` | list of hits | in the order asked |
| `order` | string | only when the request named one |

A hit has these keys, in this order: `conversation_id`, `turn`, `seq`,
`message`, `kind`, `call_id` (string or null), `name` (string or null: a
call's own name, and for a `tool_result` the name of the call with its
`call_id` in the same turn, null when the turn holds none), `created_at`
and `actor` (the turn's), `size` (the whole piece's bytes), `excerpt` (an
object, `offset` then `text`) and, only under `relevance`, `score`.

- **Matching.** Terms are the query split on white space and folded with
  per-scalar Unicode 17.0.0 lowercase, as memory search folds, and they are
  matched as substrings of the record's folded text. A `tool_call` is also
  matched on its name, in each of its chunks. An occurrence that spans two
  records, the two sides of a cut piece included, is not found. Files
  match with Unicode lowercase. Postgres decides which records hold a term
  with `ILIKE` under the database's collation and counts with Unicode
  lowercase, so the backends agree wherever the database's case folding
  agrees with Unicode lowercase, which includes all ASCII text; a record
  that only `ILIKE` matches is a candidate with counts of zero and can be
  answered under `relevance` with `score: 0`.
- **Newest.** Records holding every term, by turn time descending, then
  conversation id descending, turn descending and `seq` ascending.
- **Relevance.** Records holding at least one term, scored with memory
  ranking version 1's arithmetic (above). The documents are the candidate
  records themselves: those of the conversations, kinds and time range
  asked that hold a term. A term's count is its non-overlapping occurrences
  in the folded text, and for a call in its folded name too; a record's
  length is its folded text in scalars, and for a call its folded name and
  one more before it. Ties follow the newest order.
  `NEOSIAN_SEARCH_MAX_CANDIDATES` bounds the candidates, counted in records:
  past it the search is refused, never cut short, with `400 value_error`
  and the message `resource limit NEOSIAN_SEARCH_MAX_CANDIDATES exceeded:
  more than <maximum> records hold a search term; narrow the words, the
  kinds or the time range`. Records outnumber turns, so a common word over
  many conversations is refused sooner than on `search_turns`.
- **Excerpt.** At most 240 Unicode scalars of the record's text, starting
  up to 40 scalars before the first match there, with `offset` that start
  in zero-based scalars within the record's text (not the piece's). A call
  matched on its name alone has its excerpt from offset 0.
- **What never matches.** An open draft, a restore that is not committed
  and a redacted turn. A turn stored whole is searched as the records
  `read_records` answers for it, and one that cannot be records (above)
  refuses a search that reaches it.
- **Ids.** A conversation id outside the id grammar is
  `400 agent_conversation_id_invalid`; one that names no conversation
  holds no hit.

One failing assertion found in the tool result of the turn written above.
The excerpt is shortened here with `...`; an answer carries the text
itself, 240 scalars from scalar 31,507 of record 9. The score is the one
ranking version 1 gives a lone candidate holding each of the two terms
once, `2 * ln(4/3)` in millionths:

```json
POST /v1/conversation/search_records
{"query":"failed assertion","conversations":["cc-7f3a"],"kinds":["tool_result"],
 "order":"relevance","limit":5}

200 {"hits":[{"conversation_id":"cc-7f3a","turn":42,"seq":9,"message":2,"kind":"tool_result",
              "call_id":"toolu_01","name":"Bash","created_at":"2026-11-21T14:03:07.512000Z",
              "actor":"client:default/claude-code:7f3a","size":812345,
              "excerpt":{"offset":31507,"text":"... thread 'cache::ttl' panicked: failed assertion: left == right ..."},
              "score":575364}],
     "order":"relevance"}
```

**`store/restore_records`** moves one conversation verbatim onto an empty
target in as many requests as it needs, for an operator. The content of a
restore is one ordered stream: for each turn ascending, its header and then
its records in `seq` order; then the projections in order; then the trail
acts in order. Nothing of it is visible until a request commits it, then
the conversation appears whole.

| request field | type | rule |
|---|---|---|
| `conversation_id` | string, required | the id grammar |
| `restore` | string, required | 1 to 64 of `[a-z0-9_-]`, chosen by the client |
| `turns` | list of turn headers | ascending; each after the turn before it is complete |
| `records` | list of records in restore shape | by turn and `seq`; a turn's records follow its header |
| `projections_from`, `projections` | integer >= 0; list of projections | the index of the list's first entry among the conversation's projections; named together or not at all |
| `redactions_from`, `redactions` | integer >= 0; list of trail acts | likewise for the conversation's trail |
| `commit` | boolean, default false | after this part, check completeness and publish |
| `discard` | boolean, default false | drop the restore; no content field and no `commit` beside it |

A request that carries no item, with neither `commit` nor `discard`, is a
**status probe**: only `conversation_id` and `restore`, or lists that are
all empty. No field accepts `null`. The four lists hold at most 2,048 items
together. A refusal inside a projection or a trail act names the field by
its place, as `redactions[0].turns`.

| response field (in this order) | type | meaning |
|---|---|---|
| `restore` | string | echoed |
| `state` | `none`, `open`, `committed`, `discarded` | `none`: no such restore |
| `position` | object or null | where the stream stands; null for `none` |
| `stored_bytes` | integer | size-rule bytes this request added, 0 for a pure repeat |
| `freed_bytes` | integer | size-rule bytes this request removed |
| `digest` | hex string or null | once committed, the unit digest |

`position` has these keys, in this order: `turn` (the last header
received, 0 before any), `seq` (how many of that turn's records were
received), `complete` (whether that turn has all its records),
`projections` and `redactions` (how many of each were received).

The items of a restore, each decoded strictly:

- **A turn header** has the six keys of a turn header above. `actor` may be
  null or omitted; every other key is required. A redacted turn has
  `redacted: true`, `records: 0` and `bytes: 0`; any other turn has at
  least one record, and an empty message is one record with an empty text.
  `created_at` is a time with an offset: `YYYY-MM-DD`, `T` or one other
  separator, `HH:MM`, optionally `:SS` and up to six fractional digits,
  then `Z`, `+HH:MM`, `+HHMM` or `+HH`. It is kept as its instant and
  written in UTC with `Z`. A time with more than six fractional digits, one
  without an offset, and one whose UTC form falls outside years 0000 to
  9999 are `400 value_error`; nothing is dropped silently. A trail act's
  `created_at` is read the same way.
- **A record in restore shape** is a written record with `turn` (integer
  >= 1) in front. `tail` and `truncated` of an answered record are not
  part of it.
- **A projection** has `turn`, `kind` (`log`, `digest` or `epoch`), `text`
  and `span`, as `append_projections` takes it; `span` is 1 when omitted.
- **A trail act** has `conversation_id` (this conversation's), `turns` (a
  list of turn numbers from 1), `actor` (string or null, may be omitted)
  and `created_at`, as `turn_redactions` answers it.

Rules:

- **Verbatim.** Numbers, actors, redaction flags, projections and the
  trail are stored as sent, and a time as its instant, written in UTC. The
  caller's client is never stamped, and a null actor stays null. Turn
  numbers are ascending; they may start above 1 and skip numbers, and the
  store numbers on from the last.
- **Value ranges.** A PostgreSQL store refuses, as `400 value_error`, a
  NUL character in a header's or a trail act's `actor`, a record's `text`,
  `call_id` or `name`, or a projection's `text`; a number above
  2,147,483,647 in a turn number (of a header, a record, a projection or a
  trail act), a header's `records`, a projection's `span`, or a record's
  `seq`, `message`, `block`, `call` or `chunk`; and a `bytes` or `size`
  above 9,223,372,036,854,775,807. A FileStore accepts all of these.
- **Probe.** It creates nothing and answers the state: `none`, `open` with
  the position to resume from, or `committed` with the final position and
  the digest. After an interruption a client probes and continues its
  source from `position`. A probe is not a touch: a restore's age runs from
  its last part, a pure repeat included.
- **A place beside an empty list** is checked like the place of a first
  entry: a `projections_from` or `redactions_from` past what the restore
  holds is `restore_gap`, on a restore that does not exist as well, where
  it holds none.
- **Empty target.** The first part is `target_occupied` when the
  conversation holds a visible turn or a projection, and `restore_open`
  when another restore of the conversation is open and was touched within
  `NEOSIAN_DRAFT_SECONDS`. An older one is discarded once the part's own
  checks have passed, and its bytes are reported in `freed_bytes`.
- **Repeats are harmless.** Every item has a place in the stream. An item
  at a place already stored must be the item stored there and changes
  nothing (`restore_mismatch` otherwise). An item past the next place is
  `restore_gap`: a record before its header or ahead of its `seq`, a header
  before the turn before it is complete, a projection or an act ahead of
  its index or before the last turn is complete. A part cut at other
  boundaries than the first time is therefore harmless.
- **Order.** Within a part the turns are ascending and each turn's records
  consecutive. Turns come before projections and projections before trail
  acts; a turn after a projection, or a projection after an act, is
  `400 value_error`.
- **Completeness is checked as records arrive.** A record past its
  header's count, and records passing or ending short of its header's
  `bytes`, are `400 value_error` naming the turn, so a turn that `position`
  calls complete is complete.
- **The store's bound.** A header whose `bytes` exceed
  `NEOSIAN_MAX_RECORD_TURN_BYTES` is refused with the `413` above when it
  arrives, before any of its records.
- **Commit.** It checks that the last turn is complete and that every
  projection and trail act names turns the restore holds
  (`restore_incomplete` otherwise), and that the target is still empty
  (`target_occupied`). Then everything is published at once. Its
  `stored_bytes` add the committed marker, and the conversation row only
  when the commit creates the conversation; its `freed_bytes` are the
  bookkeeping. A commit repeated alone, a probe, and the
  final request repeated whole answer `committed` with the same position
  and digest and no bytes. Any other content after a commit, and a
  discard, are `restore_committed`.
- **An empty commit.** `commit: true` without content on a restore that
  does not exist publishes an empty restore. It answers `committed` with
  the position before any header (`turn` 0, `seq` 0, `complete` true, no
  projections, no trail acts) and the unit digest of no item,
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`. Its
  `stored_bytes` are the bookkeeping, the committed marker and, when the
  conversation is new, the conversation row, and its `freed_bytes` the
  bookkeeping. The conversation is listed nowhere and holds no turn, and
  its target is not occupied: a restore under another id still succeeds.
  The id itself is committed, so content under it and a discard are
  `restore_committed` until the conversation is erased whole. On a target
  that is occupied an empty commit is `target_occupied`, and beside
  another open restore `restore_open`, like any first part.
- **Discard.** `discard: true` removes everything staged and answers
  `discarded` with the position it had and its bytes in `freed_bytes`;
  where there is no such restore it answers `none`. Erasing the whole
  conversation and `sweep_records` remove a staged restore too.
- **A refused request changes nothing.** Every item of a part is placed
  and compared before any is stored. A conflict's `position` is the
  position before the request, and a probe answers the same.
- **A half-restored conversation never reads as complete.** Until the
  commit nothing of it is visible to any read, search or listing, and
  `last_turn_number` answers 0. An `append_turn` or a whole
  `restore_conversation` from another client meanwhile succeeds on the
  empty target, and the commit then refuses with `target_occupied`.
- **Unit digest.** SHA-256 over the concatenation, in stream order, of
  each item's own SHA-256 (its 32 raw bytes) over the item's canonical
  form: compact JSON with every key of its shape in the order given here,
  null ones included, non-ASCII raw, `span` always present, and a time as
  the store writes it, in UTC with `Z` and with six fractional digits only
  when they are not all zero. Client and server compute it as the stream
  passes, without holding the stream.

A conversation of three turns, the second erased, with one projection and
one trail act, restored in two parts. The restore id is the one the binary
derives: `i` and the first 32 hex digits of the SHA-256 of the
conversation id. Turn 1 holds a system, a user and an assistant message of
32, 500 and 1,000 bytes and turn 3 a prompt of 44 bytes (records
shortened):

```json
POST /v1/store/restore_records
{"conversation_id":"cc-7f3a","restore":"iecf9f710d465bc0ea0fe2fbba8915596",
 "turns":[{"turn":1,"created_at":"2026-10-02T09:15:00.000001Z","actor":"claude-code:7f3a",
           "redacted":false,"records":3,"bytes":1532}],
 "records":[{"turn":1,"seq":0,...},{"turn":1,"seq":1,...},{"turn":1,"seq":2,...}]}

200 {"restore":"iecf9f710d465bc0ea0fe2fbba8915596","state":"open",
     "position":{"turn":1,"seq":3,"complete":true,"projections":0,"redactions":0},
     "stored_bytes":4002,"freed_bytes":0,"digest":null}

POST /v1/store/restore_records
{"conversation_id":"cc-7f3a","restore":"iecf9f710d465bc0ea0fe2fbba8915596",
 "turns":[{"turn":2,"created_at":"2026-10-02T09:20:11Z","actor":"claude-code:7f3a",
           "redacted":true,"records":0,"bytes":0},
          {"turn":3,"created_at":"2026-10-02T09:31:40Z","actor":"claude-code:7f3a",
           "redacted":false,"records":1,"bytes":44}],
 "records":[{"turn":3,"seq":0,...}],
 "projections_from":0,"projections":[{"turn":3,"kind":"epoch","text":"set up the cache","span":3}],
 "redactions_from":0,"redactions":[{"conversation_id":"cc-7f3a","turns":[2],
                                    "actor":"client:default/ops","created_at":"2026-10-05T08:00:00Z"}],
 "commit":true}

200 {"restore":"iecf9f710d465bc0ea0fe2fbba8915596","state":"committed",
     "position":{"turn":3,"seq":1,"complete":true,"projections":1,"redactions":1},
     "stored_bytes":2077,"freed_bytes":1071,
     "digest":"6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32"}
```

The first part stores a header of 589 bytes, three records of 2,342 and
the bookkeeping of 1,071. The second stores a redacted header of 286, a
header of 589, a record of 314, a projection of 158, a trail act of 164,
the committed marker of 303 and the conversation row of 263, and frees the
bookkeeping.

**`store/sweep_records`** previews or removes stale drafts and staged
restores, for an operator. An opening and a restore clean up the
conversation they touch; nothing reaches a conversation that is not
written again. A host that runs only `serve` calls this route on its own
schedule: the core schedules nothing.

| request field | type | rule |
|---|---|---|
| `limit` | integer 1 to 100, default 50 | the most conversations visited in this call |
| `cursor` | string, 1 to 4,096 bytes | the previous call's `next_cursor` |
| `write` | boolean, default false | false previews; true removes |

| response field (in this order) | type | meaning |
|---|---|---|
| `conversations` | list | one entry for each conversation holding stale items: `conversation_id`, `drafts`, `restores` (how many were removed, or would be), `freed_bytes`, in this order |
| `next_cursor` | string or null | null when the store has no more |
| `write` | boolean | echoed |

- **Stale** means open and untouched for more than
  `NEOSIAN_DRAFT_SECONDS`. A sealed draft, a committed restore and anything
  touched since are left alone.
- **A call** visits at most `limit` conversations holding stale items, in
  bytewise conversation id order after the cursor, and removes each
  conversation's items on their own, so a call interrupted midway has
  removed whole conversations and the next call goes on. `freed_bytes` is
  the size rule's bytes the items hold: for a draft its bookkeeping and its
  records. A preview answers what the same call with `write: true` would,
  as long as nothing changes between the two.
- **The cursor** names the last conversation visited and nothing else. It
  binds neither `limit` nor `write` and confers nothing: a preview's cursor
  sent with `write: true`, or one made up, sweeps the conversations after
  the id it names, which the request could sweep anyway. A cursor that is
  not this route's, or is malformed, is `400 value_error`.
- `neosian records drafts` runs the same sweep on a FileStore from the
  command line (`neosian docs stores`).

```json
POST /v1/store/sweep_records
{"limit":2,"write":true}

200 {"conversations":[{"conversation_id":"w-11112222-0a1b","drafts":1,"restores":0,"freed_bytes":4216034},
                      {"conversation_id":"w-11112222-7c9d","drafts":0,"restores":1,"freed_bytes":38977511}],
     "next_cursor":"WyJzd2VlcC12MSIsInctMTExMTIyMjItN2M5ZCJd","write":true}
```

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
`limit` is a bound, 500 at most. Size paging version 1, above, adds
byte-bounded pages to it and to `read_turns`.

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

A narrowing of accepted legacy input does not move the wire version. Routes
and required response changes need a wire revision. The optional authorization
capability introduced with profiles is a recorded, separately versioned
extension; base handshakes and all stored shapes remain unchanged. The binary
refuses a different advertised wire version before any storage call.

## Beside the wire

`/mcp` is available when the process starts with mounts. It uses authenticated
streamable HTTP and the initialize/initialized handshake through protocol
2025-11-25. The retired Python server's 2026 single-exchange protocol is not
part of v2. See `neosian docs mcp` for lifecycle and tool scope boundaries.

Knowledge bundles and collection metadata are format-1 application content
over existing memory routes. They add no wire operation or backend capability.
The scoped CLI composes those reads/writes; it cannot claim a transaction or
atomic compare-and-create across them. `neosian docs knowledge` distinguishes
current-document ingestion from whole-store restoration.

Skill management reuses existing mounted memory read/write/list/version
operations. Skills, resources and their metadata remain normal documents;
profile revision guards need no additional storage capability. The binary
checks current dependencies/resources before serving managed instructions.

Reviewed upkeep also uses those existing routes, plus ordinary deletes. Its
proposal format adds no backend capability or DDL. Replacement uses
`expected_version`; wire 6 has no conditional delete, redaction-generation
compare-and-swap or multi-document transaction. Snapshot rechecks cannot
close every concurrent-write window. Apply/recovery therefore require paused
writers, and lost acknowledgements require current-state inspection before
preparing further work. See `neosian docs memory`.

## Optional change-feed version 1

`change_feed: {version: 1}` advertises durable per-scope state references and
explicit bounded replay. FileStore supports it; Postgres requires the separate
operator-installed changes extension 1 on schema 4. Clients refuse absent or
unsupported declarations. `changes-v1.schema.json` specifies events and pages;
`neosian docs changes` specifies ordering, atomicity, resets and retention.

`memory/changes` requires current scope read authority before storage access.
`memory/prune_changes`, including preview, requires operator and scope write
authority. Profile policies do not hide authorized raw audit references. A cursor
is scoped, opaque, at most 2,048 UTF-8 bytes and never grants authority. Unknown
fields and malformed requests refuse. Normal empty pages retain a checkpoint;
gaps are `untracked`, `retention` or `stream_reset`, never successful omission.
Events are ordered by sequence and contain references, never document bodies.
A custom backend must durably couple its mutations and events before advertising
this capability. Independently passing JSON shape validation is insufficient.

The CLI exchange format 1 composes existing scoped memory reads and change-feed
v1 into snapshots and deltas for an explicit local receiver. There is no new
HTTP capability or apply route. Current grants apply to every source read;
source histories and raw turns are excluded from the output.
`neosian schema exchange` and `neosian docs exchange` specify that separate
contract, paused-source requirement, local recovery and conflict rules.
