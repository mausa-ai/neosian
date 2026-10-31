<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mausa-ai/neosian/v2.0.1/branding/logo-dark.svg">
  <img src="https://raw.githubusercontent.com/mausa-ai/neosian/v2.0.1/branding/logo-light.svg" alt="neosian" width="280">
</picture>

# neosian

**Memory and context for the agent you already use.** Durable conversations,
agent-curated memory, skills, messages and a ledger of who did what, on
storage you own. One Rust binary; CLI, MCP and HTTP access. No model account,
Python runtime or hosted service is required.

[Documentation](https://neosian.com/docs) ·
[Releases](https://github.com/mausa-ai/neosian/releases) ·
[Changelog](CHANGELOG.md)

## Install

```sh
curl -fsS https://neosian.com/install | sh
"$HOME/.neosian/bin/neosian" setup --write
"$HOME/.neosian/bin/neosian" setup --check
```

Setup connects Claude Code, Codex, OpenCode, Muse Code, Pi and interactive
Cursor through MCP and hooks. It previews the registrations before `--write`.
Use the exact path printed by the installer if you chose another install directory.
Setup shows the running, selected and PATH executables, storage and capture policy.
`setup --check` inspects registrations and tests MCP with temporary file storage;
it leaves your selected storage unopened. Restart the client and review hook trust
after setup. Every session can leave a durable record;
the next session reads the index, recalls relevant turns and continues
explicit handoffs.
Claude Code setup also installs a bundled plugin that keeps an observed
one-hour subscription cache warm while idle, until the session closes. Expired
or uncertain caches show a warning and wait for your next message. The badge
shows refresh receipts; `/neosian-cache` provides status, on/off and duration
controls. See `neosian docs agents` for the request limits and compatibility.
Portable profiles opt into recording separately: new profiles capture off by
default.

The installer verifies the archive's checksum and installs under
`~/.neosian/bin`, linking `~/.local/bin/neosian` when that name is free.
An existing command is preserved; `status` reports PATH shadowing and the separate
MCP and hook executables. A saved registration does not prove a client connection.
Windows has a PowerShell installer and MSI on each GitHub Release.
Package-manager alternatives are `npm install -g neosian` and
`cargo install neosian --locked`. Cargo builds from source with the exact
Rust version in `rust-toolchain.toml`. Update using the original installer
or package manager. Signing and notarization remain deferred.

## Read, write and carry state

```sh
neosian memory create /memories/decision --scope user:demo --content 'Keep the reason with the decision.'
neosian memory view /memories/decision --scope user:demo
neosian memory search 'decision' --scope user:demo --json
neosian memory versions /memories/decision --scope user:demo --json
neosian export ./archive --scope user:demo
```

The default home is `~/.neosian`, overridden by `NEOSIAN_HOME` or `--root`.
FileStore keeps ordinary markdown and JSONL files, with a shared root lock
for cooperating local processes. Postgres supports distributed writers;
`neosian schema postgres` prints its operator-owned DDL. A remote state
process exposes the same storage contract over authenticated HTTP.

Memory mounts define what an agent may read or edit. Every write carries an
actor and a version; ordinary delete retains history, and operator redaction
clears content while keeping its audit skeleton. Mailbox messages, reminders,
claims and outcomes use the same store. Skills are versioned memory documents;
`handoff` and `upkeep` also ship as immutable instructions for your agent.

`upkeep scan/prepare/apply` finds review candidates, prepares guarded changes
and previews them before explicit approval. It preserves sources and rechecks
versions, permissions and content; applying requires paused writers. The
agent supplies judgment. `memory maintain --dry-run` also previews
deterministic empty/duplicate cleanup. Read `neosian docs memory` for requests,
approval boundaries and inspection after an interrupted batch.
The MCP `docs` tool reads the installed documentation and `memory_history`
reads bounded, mounted version metadata. `memory search` and MCP `search_memory`
find current permitted documents through paths, bodies and explicitly selected
metadata, with bounded excerpts and versioned source references. Search sees
new writes and erasures without rebuilding the startup index. Neosian does not
run a model or control the agent's context window. Raw conversation history is shared with
readers authorized for that store; local scope defaults are not a portable
identity or privacy boundary. Use `neosian profile init NAME --write` and
machine-local bindings to select the same authorized knowledge across agents
and machines. Profiles select sources, qualified skills and bounded context;
HTTP sources require server-enforced grants. `neosian docs profiles` covers
portable import/export and explicit local/shared capture consent.

## Bring existing knowledge

`neosian knowledge import ./notes --to /memories/notes --scope user:demo`
previews Markdown ingestion; `--write` applies after pausing other writers.
Choose your own collections and optional field rules, resolve deliberate
references, and export readable knowledge bundles with source provenance.
These use the same profiles and storage backends as memory. Full-history
archives remain available separately. Knowledge batches default to 10,000 documents
and 256 MiB with configurable budgets; imports stage bodies on disk and exports
publish complete bundles. Read `neosian docs knowledge`.

## Manage reusable skills

`neosian skill import ./review --to /memories/skills/review --scope user:demo`
previews a Markdown skill or directory package. Discover, inspect, validate,
edit, diff, revert and export use normal memory versions and mounted authority.
Optional text resources and dependencies are checked when loaded. Profiles can
select and pin current skill revisions explicitly without copying their bodies.
The agent executes instructions. Read `neosian docs skills` for the workflow,
revision guards, permission rules and paused-writer requirements.

## Review shared knowledge

Shared procedures and decisions can carry ownership, review, effective/expiry
state and explicit supersession links. `knowledge govern` prepares an upkeep
proposal; `knowledge read` and filtered search select current approved content.
Profile policies also govern injected knowledge and selected skill graphs.
Labels describe responsibility, while server grants authorize access. Read
`neosian docs governance` for approval, retrieval and erasure boundaries.

## Interfaces and compatibility

Version 2 intentionally retires the Python distribution, public SDKs,
agent/provider runtime, chat/TUI, playground, runtime guardrails and agent
evaluation product. Custom storage integrates through a conformance-tested
HTTP service. There is no supported Rust embedding API.

Memory, turn and mailbox formats remain **1**, Postgres schema **4**, and
storage wire **6**. These data and protocol versions are independent of the
application version. Existing histories and JSON metadata survive. Unsupported
YAML metadata and connection settings are refused explicitly; read
`neosian docs migration` for supported formats and upgrade steps.
Error identifiers remain reserved forever.

`neosian docs` lists every embedded page; `neosian docs TOPIC` reads it.
The versioned `neosian-docs.json` release artifact supplies those same pages
to the website. `neosian-contracts.tar.gz` contains machine contracts,
fixtures and the independent HTTP conformance runner.
`neosian schema state`, `wire`, `errors`, `profile` and
`grants`, `knowledge`, `skill`, `upkeep`, `governance` and `changes` print the
machine-readable definitions directly.

## Development

```sh
make install        # locked Python test/development tools, no product package
make lint           # ruff and black
make typecheck      # mypy --strict over tests and tools
make test           # independent binary contracts, keyless
make size           # Rust source file-size gate
make rust-lint      # fmt, clippy, deny, generated release workflow
make rust-test      # Rust unit and integration tests
make test-postgres  # explicit disposable test database DSN
make test-container # independent conformance against both image backends
make bench-hooks    # binary latency budgets over a 50 MB literal home
make docs-artifacts # versioned website input and contracts archive
```

Cargo's workspace version is the product version authority. The independent
tests retain literal v1 records and 261 frozen Python-reference cases, plus
raw CLI/MCP/HTTP, file corruption, concurrency and Postgres probes. They
import no product implementation. Python is used only for development and
tests. Node checks exercise the shipped Pi and OpenCode adapters.

Contributions use DCO sign-off (`git commit -s`) and Apache-2.0 licensing.
See [SERVICES.md](SERVICES.md) for environment settings and release accounts,
[SECURITY.md](SECURITY.md) for vulnerability reports, and
[llms.txt](../llms.txt) for the machine-readable front door.

Durable change polling is available through `changes read`, MCP `changes` and
optional HTTP change-feed version 1. Events carry stable references and explicit
gaps. File memory mutations recover through a redo journal; Postgres couples
events transactionally after its operator installs the optional changes extension.
Mailbox context can reference a document revision without copying its body.
Read `neosian docs changes` before enabling the feed or upgrading mixed writers.

## Exchange incremental memory

`neosian exchange snapshot` and `exchange delta` carry one authorized tracked
scope to a dedicated local replica. Preview/apply preserves current envelopes,
retains observed revisions and propagates erasure with an atomic checkpoint.
Pause source writers for export; reordered bundles, local edits and source gaps
refuse explicitly. Source histories, conversation turns and bindings are excluded.
Read `neosian docs exchange` for the one-way contract, recovery and bounds.
