---
title: "The command line"
summary: Versioned docs, setup, storage, memory, history and operator commands
---

# The command line

Bare `neosian` prints help. `neosian version` prints the installed version;
`neosian docs` lists shipped topics and `neosian docs TOPIC` prints a page.
`neosian COMMAND --help` is the authoritative argument grammar. Data goes to
stdout, guidance to stderr. Exit 0 is success, 1 an operation failure, 2 an
invalid invocation. Reporting and operator commands accept `--json`; streaming
protocol servers use their own framing.

| Command | Purpose |
|---|---|
| `setup`, `status` | Register clients and inspect local setup |
| `profile init/import/export/show/bind` | Create portable workspace selections and local bindings |
| `mcp install`, `record install` | Configure one integration independently |
| `update` | Check/apply a binary update or set update policy |
| `memory view/create/str_replace/insert/delete/rename` | Mounted document operations |
| `memory search` | Keyword search over current mounted documents and selected metadata |
| `memory versions/revert/redact` | Inspect history, undo the newest row, erase content |
| `memory maintain` | Aged empty/exact-duplicate cleanup; preview with `--dry-run` |
| `messages` | Durable messages, reminders, claims and acknowledgments |
| `search`, `continue` | Find recorded turns and resume a known conversation |
| `audit` | Scope ledger, optionally merged with a conversation |
| `export`, `import` | Portable archives with histories and attribution |
| `redact`, `prune` | Operator conversation erasure and retention |
| `record` | Consume a foreign agent hook payload on stdin |
| `mcp` | MCP server over stdio |
| `serve` | State HTTP and optional MCP HTTP server |
| `schema state/wire/errors/profile/grants/postgres` | Print machine contracts or schema SQL |

## Store selection and mounts

`--profile NAME` selects portable sources for memory, messages, search,
continue, MCP and record, and also configures setup/status. `NEOSIAN_PROFILE`
and local checkout bindings provide defaults. A selected profile cannot be
combined with legacy store/scope selectors. Whole-store operators refuse an
active profile; run them against an explicit legacy store from an unbound
directory. See `neosian docs profiles` for import/export, authority and capture.
Profile management returns JSON, except `profile export`, which prints TOML.

The following selection rules apply without a profile.

`--root DIR`, `NEOSIAN_POSTGRES_DSN` and `--url URL` are mutually exclusive.
`--schema NAME` applies only to Postgres. A remote client takes its bearer from
`NEOSIAN_CLIENT_TOKEN`, never argv. Without a store selection, the home is
`NEOSIAN_HOME` or `~/.neosian`. `--scope SCOPE` mounts at `/memories`;
`--mount scope=SCOPE,path=NAME[,ro|,eo]` is repeatable. An explicit mount list
replaces default user/project mounts. `--actor` records attribution.

```sh
neosian memory view / --root ./state --scope user:demo --json
neosian memory search 'deployment decision' --root ./state --scope user:demo --json
neosian memory create /memories/note --root ./state --scope user:demo --content -
neosian audit --scope user:demo --json
neosian export ./archive --scope user:demo
neosian import ./archive --root ./restored
```

`memory versions --limit N` returns newest rows. `memory revert PATH --version N`
requires N to be the latest row. `memory redact /memories --all` erases that
scope's memory content. `redact ID --all`, `--through N` or `--turns 1,3` erases
recorded turns. `prune --older-than 30d --dry-run` previews retention before
running without `--dry-run`. Ordinary deletes keep history; erasure does not.

## Installation and updates

Setup previews unless `--write` is given. In scripts use `--write --yes`, or
repeat `--client`. Preserve unrelated client settings and inspect reported
pending client-owned registrations. `--binary PATH` chooses the registered
executable. `status --json` reports paths and registrations without provider
credentials. `update --mode off|notify|auto` stores the update policy; ordinary
hooks and MCP calls never initiate update checks. Package-manager installations
should be updated using that package manager.

Version 2 retires provider configuration, chat/TUI, playground, evaluation,
reflection execution and maintenance's model stage. See `neosian docs migration`.
