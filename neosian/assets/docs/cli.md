---
title: The shell — operate memory with no Python in the loop
summary: Six commands + maintain as neosian memory, --json envelopes, exit tiers 0/1/2/130
---

# Memory from the shell

`neosian memory <command>` is the fourth transport over the same
dispatcher the function tool, the native Anthropic declaration, and
the MCP server execute. An agent with nothing but shell access
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
| `--mount scope=...,path=...[,ro]` | explicit mount; repeatable |
| `--actor NAME` | recorded on every version row (default `cli`; convention `cli:<host>`) |
| `--schema NAME` | Postgres schema (Postgres only) |

Postgres arrives only through the `NEOSIAN_POSTGRES_DSN` environment
variable — there is no `--dsn` flag (argv is world-readable), and
`--root` with the DSN set is refused. Exactly one of the two stores
must be reachable.

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

`--json` prints the memory tool's result envelope verbatim, one JSON
object on stdout, exit 0/1 by its `success` field:

```bash
neosian memory view / --root .neosian/memory --scope user:me --json
```

Argv-tier errors (exit 2) stay argparse text on stderr — a shell
answers grammar before any envelope exists.

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

## One writer per root

A FileStore root is owned by one writer at a time. Do not run
`neosian memory` writes against a root an MCP server (or an embedding
application) is serving — route multi-writer needs to Postgres. The
full rule: `neosian docs topology`.
