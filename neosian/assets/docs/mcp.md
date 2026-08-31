---
title: MCP — the same memory, served to any client
summary: Serve over stdio, register with neosian mcp install, one writer per root
---

# The MCP memory server

The same stores, served to any MCP client — Claude Code, Claude
Desktop, Cursor — over stdio. Requires the `mcp` extra
(`neosian[mcp]`); the core install refuses with an install hint, never
a traceback. For MCP over the network, the state process mounts this
same factory's server at `/mcp`: `neosian serve` with mounts
(`server` extra, `neosian docs topology`).

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
tool uses; the index refreshes per connection.

Hosts that embed the server in their own transport use
`create_memory_server` from `neosian.mcp`.

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
  `cursor` → `~/.cursor/mcp.json`.
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

## One writer per root

An MCP server serving a FileStore root **owns** that root while it
runs. Do not write to the same root with `neosian memory` or an
embedding application at the same time — reads are fine, concurrent
writers are not arbitrated on files. Multi-writer needs route to
`PostgresStore` or to the state process (`neosian serve`), where one
process owns the root for every client. The full rule:
`neosian docs topology`.
