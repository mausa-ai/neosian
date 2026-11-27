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
| `login`, `logout` | Connect a machine, refresh host-managed profiles or disconnect it. See `neosian docs login`. |
| `setup`, `status` | Register clients, refresh existing registrations and inspect local setup; `status` also shows what recording keeps on this machine and what each destination last answered (see `neosian docs agents`) |
| `statusline --client claude-code` | Display cache state from Claude Code's statusline JSON; setup installs its observation bridge |
| `profile init/import/export/show/bind` | Create portable workspace selections and local bindings |
| `skill` | Import/export, discover/load, inspect/validate, edit/diff/revert and select/pin reusable skills. See `neosian docs skills`. |
| `knowledge import/collection/validate/references/export` | Preview scoped Markdown ingestion, optional structures and readable bundles |
| `knowledge govern/read` | Prepare a review-state proposal or retrieve current knowledge under an explicit policy |
| `mcp install`, `record install` | Configure one integration independently |
| `update` | Check/apply a binary update or set update policy |
| `memory view/create/str_replace/insert/delete/rename` | Mounted document operations |
| `memory search` | Keyword search over current mounted documents and selected metadata, best match first, newest or by path |
| `memory versions/revert/redact` | Inspect history, undo the newest row, erase content |
| `memory maintain` | Aged empty/exact-duplicate cleanup; preview with `--dry-run` |
| `memory heat` | Which documents agent sessions read (7/30-day, all-time, last read); `--related` lists what the same sessions also read; never reorders the index |
| `upkeep scan/prepare/inspect/apply` | Bounded findings, guarded proposals, preview and explicitly approved changes |
| `changes read/prune` | Poll durable scope changes or preview/apply event retention |
| `messages` | Durable messages, reminders, claims and acknowledgments |
| `search`, `continue` | Find recorded turns, best match first or newest, within optional turn times; resume a known conversation |
| `audit` | Scope ledger, optionally merged with a conversation |
| `export`, `import` | Portable archives with histories and attribution |
| `redact`, `prune` | Operator conversation erasure and retention |
| `records enable` | Let a FileStore keep turns written in parts as bounded records; see `neosian docs stores` |
| `records drafts [--discard] [--json]` | List stale drafts and staged restores in a FileStore, a PostgreSQL store or a server named with `--url`, one JSON line per conversation. Add `--discard` to remove them. Never runs through a profile; see `neosian docs stores`. |
| `records migrate [--conversation ID]... [--json]` | Convert eligible whole turns in a FileStore or a PostgreSQL store with records enabled. Prints one JSON line per conversation. See `neosian docs stores`. |
| `records fold [--conversation ID]... [--discard-pending] [--json]` | Fold a local store's records into whole turns, one JSON line per conversation; see `neosian docs stores` |
| `record` | Consume a foreign agent hook payload on stdin; a session start's context begins with the recording notices not yet told, each shown once (see `neosian docs agents`) |
| `record pause <conversation-id>`, `record resume <conversation-id>` | Pause one profile conversation locally, or resume capture from its next user prompt |
| `record retry [--json]` | End every wait and block of this home's recording destinations; the next hook event lands their queued turns. It names each destination it unblocked by its host; see `neosian docs agents` |
| `record held [--json]` | List held turns (when each closed, its bytes, its reason) and the pending turns this environment does not serve. `--discard ID` or `--discard --all` deletes held turns; `--retry ID` or `--retry --all` puts them back at the end of their conversation's queue, and the next hook event lands them. See `neosian docs agents` |
| `native-memory add/list/sync/remove` | Register explicit native Markdown sources for guarded local capture and hook reminders |
| `mcp` | MCP server over stdio |
| `serve` | State HTTP and optional MCP HTTP server |
| `schema state/wire/errors/profile/grants/knowledge/skill/upkeep/governance/changes/postgres/changes-postgres/records-postgres` | Print machine contracts or schema SQL |

For PostgreSQL turn records, `neosian schema records-postgres` prints the
version 1 installation SQL. `--version 1` selects the same script.
`--version 2` prints the SQL to upgrade an existing version 1 installation
so agent-written turns can be folded. Use `--schema NAME` to select the
database schema. See `neosian docs stores` for privileges and downgrade.

## Store selection and mounts

`--profile NAME` selects portable sources for changes, upkeep, skill, knowledge, memory, messages, search,
continue, MCP and record, and also configures setup/status. `NEOSIAN_PROFILE`
and local checkout bindings provide defaults, followed by the logged-in machine default. A selected profile cannot be
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
`import`, and `export` from a FileStore or PostgreSQL, carry every erasure act
of a conversation in the order it was stored. `export --url` orders acts by
time and refuses a conversation whose server answers 500 erasure acts, the
most its audit read shows; export that store with `--root` or
`NEOSIAN_POSTGRES_DSN` instead. `import --url` into a server that keeps
records restores each conversation in parts of at most 983,040 bytes and
publishes it whole at the end. After an interruption or a refusal, run the
same command again: it resumes from what the server holds. See
`neosian docs stores`.

## Installation and updates

Setup previews unless `--write` is given. In scripts use `--write --yes`, or
repeat `--client`. Preserve unrelated client settings and inspect reported
pending client-owned registrations. `setup --write` removes the neosian hook
from the group that holds it, keeping that group's other hooks, and appends a
fresh group with the neosian hook at the end of the event, so a matcher set on
the old group no longer applies to it. It writes a symlinked file at its target
and keeps the file's mode, and because you asked for it, it replaces a file
even when the file is read-only. `--binary PATH` chooses the registered
executable. `status --json` reports paths and registrations without provider
credentials. `neosian update` installs an available stable release through this
native installation's updater and verifies the installed version. It then runs
the installed release's `setup --refresh`, whose output passes through as it
runs, and ends by saying that running clients use the new release once
restarted. `--check` previews the install without installing or refreshing;
`--write` remains accepted for existing scripts.
Those two flags cannot be combined. The command refuses major-version jumps
and an updater that does not belong to this installation.
`update --mode off|notify|auto` only stores the policy for passive checks at
interactive `status`; it does not install anything. That policy never prevents
an explicit `neosian update`. An `auto` install at `status` refreshes too and
prints its report on stderr before the status report. Ordinary hooks and MCP
calls never initiate update checks. Package-manager installations should be
updated using that package manager, followed by `neosian setup --refresh`.

`setup --refresh` brings the registrations that already exist to the running
release: the same clients and the same store selection, at the user level. It
keeps each registered command line word for word and changes only what the
release owns and needs to change. A hook group that already carries the
neosian command stays as it is, with any hooks or settings you added to it; a
missing hook event gains a new group, and an event the release no longer uses
loses only the neosian hook. An MCP entry gains members it lacks, except
OpenCode's `enabled`, which is never added, and keeps every value it has. The
OpenCode and Pi adapters, the Codex skill and the Claude Code plugin are
replaced when their bytes differ; a managed skill you deleted while its receipt
still says enabled is installed again, so use `--codex-skills off` to keep it
away. A file that is
already current is not written; a symlinked file is written at its target and
keeps its mode. It connects no new client, creates no client home, adds no
skill or plugin that was never installed or that Claude no longer lists, and
keeps `--codex-skills off` and `--claude-cache off`. Project files in the
current directory are listed, never edited, apart from the move below.

The refresh also moves each client from the server name `neosian-memory` to
`neosian`, so rules and agents name its tools `mcp__neosian__…` in Claude
Code, Codex, Pi and Muse Code and `neosian_…` in OpenCode. neosian's own entry under the old key is removed and the same entry
written under the new one, in every scope the client reads (user, Claude
Code's local scope, the projects the client's own index records and the current
directory), a client whole or not at all, with a copy of each file first under
the neosian home's `migrations` folder. It moves a client by itself only when
no other server is named `neosian` there and the client's own settings and the
frontmatter of its agents, skills and commands hold no `neosian-memory` or
`mcp__neosian` outside allow lists; otherwise the client stays on
`neosian-memory`, which keeps working, and the report lists what it found.
`setup --refresh --rename` moves it anyway. `setup --undo-rename [CLIENT]`
puts clients back on `neosian-memory` and keeps them there until `--rename`.
OpenCode's `opencode.jsonc` is read with comments and trailing commas allowed,
and the entry there moves in place with every comment kept. neosian never
edits a rule, hook, agent or plugin you wrote. `status` says which name each
client is on; `setup --check` and the refresh fail where both names are
registered for the user level or one project.

A part the refresh cannot change faithfully is left alone, and the report says
why and what to fix: a registration for another installation or with words
this release does not write, a read-only file or a directory its owner cannot
write, a symbolic link whose target does not exist, a locally edited skill, a
statusline without the cache bridge, or the `claude` CLI missing when the
plugin needs it. Leaving a part alone is not a failure: the client's other
parts proceed and the exit stays 0. Only a write that fails makes a client
fail. The plugin step, which runs Claude's own CLI, goes before any other write
for that client, so a CLI failure leaves none of the refresh's documents
written. The files this run wrote are put back unless the client changed them
meanwhile; the report lists every file that still differs from before the
refresh, and the exit is 1, so `update` also exits 1 and keeps the new binary. The retry is always
`neosian setup --refresh`. There is no atomicity across a crash or an
interrupt: each file is replaced whole, and running `setup --refresh` again
completes a partly refreshed client. The report names the clients whose files
changed as the ones to restart. `setup --refresh --json` prints one report and never
prompts; `update --json` carries it as `refresh`, which is `null` when nothing
was installed. `update` runs exactly `setup --refresh` (with `--json` for a
JSON update) on the installed executable, and later releases keep accepting
that command. An update performed by 2.18.0 or earlier does not hand over; run
`neosian setup --refresh` once after it.

Claude Code setup also installs the bundled cache plugin and composes the
observation bridge with an existing statusline. Keep-warm defaults on for an
observed one-hour subscription cache while idle, until the session closes.
Expired or uncertain caches wait for the next normal message. Use
`setup --claude-cache off --write` to disable it and restore the prior statusline.
`/neosian-cache` in Claude shows status and accepts `on`, `off` or a duration
such as `2h`. The plugin also tells the agent once when a 5-hour or weekly
usage limit is near (a notice at 85%, a wrap-up note at 95%, less a small
reserve while keep-warm runs and the reset is near, or about ten minutes left); it never submits a
prompt or stops a turn, and `usageNotes` in `/config` turns the notes off.
Keep-warm pauses at a reached or nearly exhausted limit and otherwise waits
warm for a near reset. See `neosian docs agents` for compatibility and request limits.

Version 2 retires provider configuration, chat/TUI, playground, evaluation,
reflection execution and maintenance's model stage. See `neosian docs migration`.

Codex setup installs the native `$neosian-upkeep` skill alongside MCP and hooks.
`setup --client codex --codex-skills off --write` removes its managed entry and
persists the opt-out. `--codex-skills on` restores it; the default `auto` keeps
that choice. Updates preserve foreign files and refuse local edits.
`setup --check` reports missing or outdated entries. See `neosian docs agents`.

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
