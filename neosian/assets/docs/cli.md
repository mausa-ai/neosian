---
title: The shell — operate memory with no Python in the loop
summary: Six commands + the operator verbs as neosian memory, --json, exit tiers 0/1/2/130
---

# Memory from the shell

`neosian memory <command>` is the shell transport over the same
dispatcher the function tool, the native Anthropic declaration, the
MCP server, and the state process's HTTP wire execute. An agent with nothing but shell access
operates the same memory the runtime transports serve.
`python -m neosian.memory` is the sandbox-safe twin for a venv whose
bin is not on PATH.

## The grammar

```
neosian memory view [PATH] [--view-range START END]   # PATH defaults to /
neosian memory create PATH --content TEXT
neosian memory str_replace PATH --old-str TEXT --new-str TEXT
neosian memory insert PATH --insert-line N --insert-text TEXT
neosian memory delete PATH
neosian memory rename OLD_PATH NEW_PATH
neosian memory maintain [--model MODEL] [--min-age-days N]
neosian memory versions PATH [--limit N]
neosian memory redact PATH [--all]
neosian memory revert PATH --version N
```

`view /` renders the memory index — the first command to try. `-` as
the value of `--content` / `--new-str` / `--insert-text` reads stdin
(the heredoc idiom; exactly one payload flag per command):

```bash
neosian memory create user/prefs.md --content - \
    --root .neosian/memory --scope user:me <<'EOF'
User prefers concise answers.
EOF
```

## Store flags (on every command)

| flag | meaning |
|---|---|
| `--root DIR` | FileStore root (created on first write) |
| `--scope SCOPE` | single read-write mount of SCOPE at `/memories` (the sugar) |
| `--mount scope=...,path=...` | explicit mount; repeatable; append `,ro` (read-only) or `,eo` (edit-only) |
| `--actor NAME` | recorded on every version row (default `cli`; convention `cli:<host>`) |
| `--schema NAME` | Postgres schema (Postgres only) |

Postgres arrives only through the `NEOSIAN_POSTGRES_DSN` environment
variable — there is no `--dsn` flag (argv is world-readable), and
`--root` with the DSN set is refused. Exactly one of the two stores
must be reachable. An edit-only mount (`eo`) fixes its document set:
existing documents stay editable, but nothing may be created, deleted,
or renamed there — pre-created layouts the agent works within.

## Exit tiers, everywhere

- **0** — success.
- **1** — the command ran and failed: a corrective failure rendered
  as `error: [code] message` plus a `hint:` line on stderr.
- **2** — the invocation was wrong: grammar, an unknown name, an
  invalid scope or mount. Nothing is constructed on this tier.
- **130** — interrupt.

stdout carries the artifact; stderr carries guidance — redirecting
stdout always captures something well-formed.

## --json

On the six commands, `--json` prints the memory tool's result envelope
verbatim, one JSON object on stdout, exit 0/1 by its `success` field:

```bash
neosian memory view / --root .neosian/memory --scope user:me --json
```

`maintain` and the operator verbs print their own envelopes instead
(described below) — still exactly one JSON object on stdout. Argv-tier
errors (exit 2) stay argparse text on stderr — a shell answers grammar
before any envelope exists.

## maintain — the gardener

`maintain` is not one of the six dispatch commands: it runs the
maintenance pass over the writable mounts (`neosian docs memory`).
Keyless by default — prune empty documents, merge byte-identical
duplicates keeping the oldest — and `--model MODEL` adds the semantic
pass (merge overlapping, prune stale, promote), which needs that
provider's API key: a missing key is refused at construction, never a
silent half-pass. `--min-age-days N` (default 7) protects recently
updated documents from deletion. Its `--json` envelope is its own —
`{"writes": [...], "model", "usage", "cost_micro_usd"}` — and a
requested model stage that fails exits 1 and says so on stderr while
the deterministic actions stand.

## The operator verbs — audit and remedy

`versions`, `redact` and `revert` sit beside `maintain` on the operator
side of the line: keyless acts over the store, never part of the
agent-facing six-command vocabulary.

**`versions PATH [--limit N]`** lists a document's version rows newest
first. Text output is the audit trail without content — one line per
row (version, action, actor, timestamp). `--json` carries every row's
**full content**: that is the point-in-time read, and it means history
reveals everything a document ever held — `redact` is the only eraser.
Empty history is an answer (exit 0), not an error.

**`redact PATH [--all]`** clears content everywhere for one document —
current state and every version row — preserving the audit skeleton
(paths, versions, actors, timestamps). A mount root redacts the whole
scope, but only with the explicit `--all`; without it the grammar
refuses. Redaction is the one irreversible act: the skeleton is
deliberately not restorable, and `revert` refuses redacted history.
Its `--json` envelope is `{"path", "scope_wide", "matched"}`.

**`revert PATH --version N`** undoes one write: `N` names the row to
undo (find it with `versions`) and must be the newest. One rule covers
every case — no live document before row N means delete, otherwise the
prior content comes back — and the revert *appends* a new version row,
never rewriting history. Its `--json` envelope is the write receipt's
fields (`command`, `path`, `version`, `previous_path`).

## One writer per root

A FileStore root is owned by one writer at a time. Do not run
`neosian memory` writes against a root an MCP server, a `neosian
serve` process, or an embedding application is serving — route
multi-writer needs to Postgres or to the state process itself. The
full rule: `neosian docs topology`.
