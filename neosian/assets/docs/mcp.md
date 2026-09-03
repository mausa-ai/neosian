---
title: MCP — the same state, served to any client
summary: memory and recall_turn over stdio; neosian mcp install; one writer per root
---

# The MCP server

The same stores, served to any MCP client — Claude Code, Claude
Desktop, Cursor, Codex, OpenCode — over stdio. Requires the `mcp` extra
(`neosian[mcp]`); the core install refuses with an install hint, never
a traceback. For MCP over the network, the state process mounts this
same factory's server at `/mcp`: `neosian serve` with mounts
(`server` extra, `neosian docs topology`).

## The state set

The memory server becomes the state server one tool at a time. Today it
serves two:

- **`memory`** — the six commands over the mounts (`neosian docs
  memory`), the same definition the function tool carries.
- **`recall_turn(turn, conversation)`** — one turn of a recorded
  conversation, verbatim: a hook-fed session of another agent
  (`neosian docs agents`), a neosian `Conversation`, anything the store
  holds. `conversation` is required here — the server has no
  conversation of its own — and any id the store holds is addressable:
  which conversations are shareable is the host's decision, never the
  library's. The ids are in the memory index under `sessions/` and in
  the "where we left off" block a `SessionStart` hook prints.

## Serve

```bash
python -m neosian.mcp --root ~/.my-agent/memory --scope user:me
```

The store flags are the same grammar as `neosian memory`: `--scope`
is the single-mount sugar, `--mount scope=...,path=...` is repeatable
(append `,ro` for read-only or `,eo` for edit-only), `--actor`
defaults to `mcp`
(convention `mcp:<host>`), and Postgres arrives only through
`NEOSIAN_POSTGRES_DSN` — never an argv flag. The server's
instructions carry the same memory index and prompt pack the function
tool uses; the index refreshes per connection. Every shipped store
keeps conversations too, so `recall_turn` is always on the list.

Hosts that embed the server in their own transport use
`create_memory_server` from `neosian.mcp`; passing their conversation
store as `conversations=` adds `recall_turn` beside `memory`.

## Register a client

`neosian mcp install` prints the exact registration for a client —
and applies it only when you ask:

```bash
neosian mcp install --client claude-code --root ~/.my-agent/memory --scope user:me
neosian mcp install --client claude-desktop ... --write
```

- Print mode (the default) puts the paste-able `mcpServers` JSON
  fragment on stdout and the target path plus guidance on stderr.
- `--write` merges the entry into the client's config file,
  preserving every other key. Targets: `claude-code` → the project's
  `./.mcp.json`; `claude-desktop` → its platform config file;
  `cursor` → `~/.cursor/mcp.json`; `codex` → `~/.codex/config.toml`
  (TOML, print-only: stdout is the `[mcp_servers.neosian-memory]`
  table and stderr the `codex mcp add …` line that applies it —
  Codex's own CLI is the writer, so `--write` is refused); `opencode` →
  the project's `opencode.json`, the entry under `mcp` in OpenCode's own
  shape (`type: local`, one `command` array).
- A client whose config directory does not exist is refused (exit 1)
  — neosian never creates another program's config home. Install the
  client first.
- The registration embeds the resolved settings: an absolute `--root`
  (clients spawn servers from arbitrary directories), mounts in
  canonical `--mount` form, and the current interpreter's absolute
  path (GUI clients do not inherit your shell's PATH). A Postgres
  registration never contains the DSN — set `NEOSIAN_POSTGRES_DSN` in
  the client's own environment.
- `--json` prints one machine-readable envelope instead.

The record half — a foreign agent's hooks — is `neosian record install`,
which renders the same mount layout from the same flags
(`neosian docs agents`).

## One writer per root

An MCP server serving a FileStore root **owns** that root while it
runs. Do not write to the same root with `neosian memory` or an
embedding application at the same time — reads are fine, concurrent
writers are not arbitrated on files. Multi-writer needs route to
`PostgresStore` or to the state process (`neosian serve`), where one
process owns the root for every client. The full rule:
`neosian docs topology`.
