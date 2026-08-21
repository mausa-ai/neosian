# The Ecosystem Contract

> Status: **v1, frozen** (2026-08-18; last amended 2026-08-21 — §12 log).
> This document is canonical HERE; hosts
> (first among them: neosae-kit, via its `docs/DESIGN.md §5.7`) reference it.
> ECOSYSTEM states **what** is frozen; [DESIGN.md](DESIGN.md) says why and how
> (working conventions: [CLAUDE.md](CLAUDE.md); env keys: [SERVICES.md](SERVICES.md));
> if they disagree, that is a bug fixed the same session — and for hosts,
> ECOSYSTEM wins. Changing anything below is governed by §12.

## §0 What this is

The seams a host application may build on. Neosian names no host; neosae-kit is
host #1 and gets no private favors — every guarantee below is available to any
consumer on the same terms.

## §1 Dependency direction

Hosts depend on neosian; never the reverse. Neosian never imports, names, or
assumes a host: no async-DB assumption, no web framework, no session object, no
tenant model. A host's entire translation layer is one adapter (in the kit:
`app/modules/neosian/wiring.py`).

## §2 Scope grammar

A memory scope is `<type>:<id>` segments joined by `/`:

```
scope   := segment ( "/" segment )*
segment := type ":" id
type    := [a-z][a-z0-9_]{0,31}
id      := [A-Za-z0-9_.-]{1,128}     # "." and ".." alone are rejected
```

≤ 8 segments, ≤ 512 characters total; `/` and `:` are structural and excluded
from `id`. Canonical types `user` and `org` are the kit's BillingSubject
serialization (`user:<uuid>`, `org:<uuid>`) — a documented convention, never an
enforced vocabulary. **Neosian validates shape and never interprets meaning**:
no containment, no inheritance, no routing on type. Hierarchy is explicit
mounts, chosen by the application.

## §3 The four token classes

`input_tokens · output_tokens · cache_read_tokens · cache_write_tokens` —
provider-flavored names are an internal mapping problem; hosts meter on these
four and nothing else. `input_tokens` always means non-cached input.

## §4 Money

Integer **micro-USD**, everywhere. Pricing tables are int µ$ per MTok with an
as-of stamp; cost computation uses ceiling division (never undercount);
unpriced models yield `None`, never 0. Floats and dollar signs exist only at
display edges. Neosian pricing is observability; a host's rate card is billing
truth.

## §5 Event vocabulary

Streaming yields typed events — `ready, content, reasoning, tool_call,
tool_result, tool_progress, blocked, done, error` — with frozen payload models
and an exported JSON Schema (`python -m neosian.schemas events`). Wire form is
SSE-compatible: `event: <name>` + one compact single-line JSON object,
ASCII-escaped so no model output can break framing. Sequence numbers start
at 1. Terminal frames (`done`/`error`/`blocked`) carry summed usage plus
per-model splits. **The error frame carries a machine code and no message
text** — it cannot leak internals by construction. Keepalive is host territory
(SSE comments on the host's timer); `tool_progress` is neosian's and means
"this tool is still running", not liveness. The kit consumes this through
`packages/api-client`'s `postSSE`.

## §6 Error-code idiom

`NeosianError` is exported; every exception carries `code: ClassVar[str]` — a
stable snake_case machine code under a closed family prefix
(`neosian_|agent_|llm_|tool_|guardrail_|memory_|prompt_|playbook_|blackboard_|eval_`)
— plus `retryable: bool`. Codes are **append-only**: deprecate, never repurpose
or rename. The conversation layer's codes ship under `agent_`
(`agent_conversation_*`, since v0.60.0); a `conversation_` prefix is
**deliberately declined** — the codes were live before the seam froze and the
append-only rule forbids renaming them (amended 2026-08-21). Provider identity and HTTP status are preserved structurally, never
stringified away. The host maps codes 1:1 into its envelope and owns 100% of
user-facing text; a new code means a new row in the host's i18n (kit:
`errors.json` + ROUTED_CODES).

## §7 The FakeProvider guarantee

A deterministic, scriptable, keyless provider ships as public API
(`neosian.fake`), versioned with everything else: canned responses, scripted
tool calls, assertable token counts, failure injection. A host's default test
tier and offline dev loop boot with **zero third-party accounts**.

## §8 Prompts as data

Every prompt neosian ships — guardrail policies, built-in tool descriptions,
the memory prompt pack — lives in data files (YAML registry with `{{var}}`
interpolation; markdown + frontmatter for documents), loaded and validated at
import, overridable by registry key. Never prose in Python.

## §9 Timestamps

Timezone-aware UTC internally; ISO-8601 `Z` on the wire. A naive datetime at
any seam is a contract violation, rejected at the boundary.

## §10 The ABC is the contract

`MemoryStore` and `ConversationStore` are async, own no connection, commit no
transaction, issue no DDL; `MemoryStore` additionally exposes scope-wide
`redact()`. `FileStore` and `PostgresStore` implement both seams as
**reference implementations for standalone users**; a host may implement its
own — the kit does, over its synchronous sessions and its own
`alembic/versions/neosian/` branch, and never uses neosian's PostgresStore.
The shipped `MemoryStoreContract` and `ConversationStoreContract` conformance
test-kits are what keep host implementations honest.

## §11 Vendoring discipline

Hosts vendor only from **annotated release tags** `v<X.Y.Z>` — never master.
The vendored snapshot records its tag; the host keeps a version-match test.
From `v1.0.0` the seams in this document are **SemVer-guaranteed**: a seam
break lands only at a major bump; additions land at a minor.

## §12 Governance

A seam change requires an entry in **both** ledgers — neosian
[DESIGN.md](DESIGN.md) §12 and the kit's `docs/DESIGN.md` §15 — in the same
session-pair; either repo may refuse. Log:

| date | change | neosian tag | kit ledger # |
|---|---|---|---|
| 2026-08-18 | Contract v1 written | — (pre-NH) | #205 |
| 2026-08-21 | 1.0 amendment: §10 gains `ConversationStore` + `ConversationStoreContract`; §6 blesses `agent_conversation_*` (declines a `conversation_` prefix); §11 SemVer-guaranteed from the eventual v1.0.0; sweeps the public message codec (neosian #22) and `CompactionBlock` in the content union (neosian #46) | v0.70.0 | #206 (kit 73dcd39) |
