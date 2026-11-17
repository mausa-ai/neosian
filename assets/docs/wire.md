---
title: "The wire: the state process's HTTP contract"
summary: Storage routes, optional memory search, authorization, paging and wire formats
---

# The wire

`neosian serve` exposes durable state through wire 6. This page documents
its twenty-one base storage POST routes, optional search/change routes, and two GET routes. Literal fixtures and
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
binary's own remote client always sends 2.16.0's request first; only when a
server that advertises size paging refuses it with `value_error` (other than
the candidate bound), or the answer passes the client's own
`NEOSIAN_MAX_RESPONSE_BYTES`, does it ask again in pages (below the server's
1 MiB in-memory spool). A page refused for its size too reports the first
refusal; any other failure reports itself.

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
missing parameter, for invalid store arguments (an empty message
list, a negative `after`) and for a request past an operation budget
(the message names the setting), and `forbidden`, for the allowance. A
malformed nested object (a message without `role`, an archive document
without `actor`) is `value_error` with the message
`malformed request: 'role'`. A `500` carries no envelope and is the
driver's problem, never a store answer.

## Spooled answers

An answer that can grow with stored content is written item by item into a
spool while the server reads the store: `memory/read`, `memory/versions`,
`memory/history`, `conversation/read_turns`, `conversation/read_projections`
and `conversation/search_turns`. An answer stays in memory while it is at most
1 MiB; past that the whole answer goes to a private temporary file and is
charged against `NEOSIAN_MAX_TEMP_BYTES` (512 MiB by default) for that one
answer. The server lets go of the store (the file store's lock, a Postgres
snapshot, transaction and connection) before it sends anything, then sends
the spool with its `Content-Length`. The bytes are exactly those of the whole
answer. A slow reader costs an open file and disk until it finishes; it never
holds the store, so other requests are not delayed.

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

The temporary directory (`TMPDIR`, or the platform's) must be writable and
disk-backed. A memory-backed one (tmpfs) counts against a container's memory
limit and defeats the bound. When no spool file can be made or written (an
unwritable directory, a full disk, a directory removed while in use), the
answer is finished in memory, as 2.16.0 held it, within
`NEOSIAN_MAX_RESPONSE_BYTES` (64 MiB by default), and refused past that with
`value_error` naming that setting; it is never a bare `500` or a cut body.
`neosian serve` checks the directory at startup and says once, on its log, when
it cannot spool there. Each server keeps its spools under its own locked
directory, `.neosian-serve-<uuid>`, removed when it stops; one left by a
server that was killed is removed by the next server started with the same
temporary directory. Disk use is the answers being sent at once, each at most
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
first refusal only when its smaller requests are refused for their size too;
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
| `conversation/search_turns` | `query` req; `conversations` (a list of ids), `limit` (default 50, at most 500); `order`, `since`, `before` with history ranking; `page_bytes`, `continuation` with size paging | `turns`; `order`, `scores` with history ranking; `continuation` with size paging |
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
`read_turns` shape. History ranking version 1, below, adds orders and
turn-time bounds; a request without its fields answers exactly this.

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
