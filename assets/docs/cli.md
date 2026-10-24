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
| `skill` | Import/export, discover/load, inspect/validate, edit/diff/revert and select/pin reusable skills. See `neosian docs skills`. |
| `knowledge import/collection/validate/references/export` | Preview scoped Markdown ingestion, optional structures and readable bundles |
| `knowledge govern/read` | Prepare a review-state proposal or retrieve current knowledge under an explicit policy |
| `mcp install`, `record install` | Configure one integration independently |
| `update` | Check/apply a binary update or set update policy |
| `memory view/create/str_replace/insert/delete/rename` | Mounted document operations |
| `memory search` | Keyword search over current mounted documents and selected metadata |
| `memory versions/revert/redact` | Inspect history, undo the newest row, erase content |
| `memory maintain` | Aged empty/exact-duplicate cleanup; preview with `--dry-run` |
| `upkeep scan/prepare/inspect/apply` | Bounded findings, guarded proposals, preview and explicitly approved changes |
| `changes read/prune` | Poll durable scope changes or preview/apply event retention |
| `messages` | Durable messages, reminders, claims and acknowledgments |
| `search`, `continue` | Find recorded turns and resume a known conversation |
| `audit` | Scope ledger, optionally merged with a conversation |
| `export`, `import` | Portable archives with histories and attribution |
| `redact`, `prune` | Operator conversation erasure and retention |
| `record` | Consume a foreign agent hook payload on stdin |
| `mcp` | MCP server over stdio |
| `serve` | State HTTP and optional MCP HTTP server |
| `schema state/wire/errors/profile/grants/knowledge/skill/upkeep/governance/changes/postgres/changes-postgres` | Print machine contracts or schema SQL |

## Store selection and mounts

`--profile NAME` selects portable sources for changes, upkeep, skill, knowledge, memory, messages, search,
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

## Knowledge imports and structure

`knowledge import DIR --to /memories/notes --scope user:demo` previews ordinary
Markdown ingestion. `--write` applies with other writers paused. Collection
rules, reference resolution and current-document bundles are described in
`neosian docs knowledge`; `neosian schema knowledge` prints their format-1
contract. This scoped workflow supports profiles. Top-level archive operators
retain their full-history restoration and operator authority requirements.

`skill` storage commands share memory selection flags. Profile selection changes
require explicit `--profile`; select/unselect/unpin do not open storage. All
changes preview until `--write`; imports require `/mount/skills/name`, default
to conflict refusal and accept explicit `--replace`. Edit/revert require
`--expect-version`; pause writers for changes and export. Results are JSON.

`upkeep scan [PATH]` returns bounded quality findings. `prepare REQUEST_FILE`
prints a proposal without writing memory; `apply PROPOSAL_FILE` previews it.
Apply requires `--write --approve PROPOSAL_ID` and paused writers. `inspect
PROPOSAL_FILE` compares current state, including after a partial or interrupted
apply. These commands share memory/profile selection flags and return JSON.
Read `neosian docs memory` for literal requests, bounds, authority, recovery
and concurrency limits; `neosian schema upkeep` prints the format-1 shapes.

`changes read /mount` returns up to 100 reference-only events, including a durable
cursor on empty pages and explicit reset/retention gaps. `changes prune /mount
--through CURSOR` previews retention; `--write` applies at most 10,000 events.
Read `neosian docs changes` for authority, reconciliation and backend setup.

`exchange snapshot /mount FILE --paused` creates a current-memory bundle;
`exchange delta /mount FILE --after PREVIOUS --paused` uses a previous bundle
or checkpoint file. `exchange inspect FILE` validates and describes it.
`exchange apply FILE --root DIR --scope SCOPE` previews; `--write` commits
to that local replica. `exchange status --root DIR --scope SCOPE` recovers
and reports its checkpoint/conflicts; `--checkpoint-only` prints reusable
checkpoint JSON. Source commands share memory/profile flags. Read `neosian docs
exchange` and `neosian schema exchange` before using the one-way workflow.
