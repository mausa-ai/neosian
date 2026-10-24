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
neosian setup --write
neosian status
```

Setup connects Claude Code, Codex, OpenCode, Muse Code, Pi and interactive
Cursor through MCP and hooks. It previews the registrations before `--write`.
Restart the client after setup. Every session can leave a durable record;
the next session reads the index, recalls relevant turns and continues
explicit handoffs.
Portable profiles opt into recording separately: new profiles capture off by
default.

The installer verifies the archive's checksum and installs under
`~/.neosian/bin`, linking `~/.local/bin/neosian` when that name is free.
An existing command is preserved; `status` reports PATH shadowing.
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

`memory maintain --dry-run` previews deterministic empty/duplicate cleanup.
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

## Interfaces and compatibility

Version 2 intentionally retires the Python distribution, public SDKs,
agent/provider runtime, chat/TUI, playground, runtime guardrails and agent
evaluation product. Custom storage integrates through a conformance-tested
HTTP service. There is no supported Rust embedding API.

Memory, turn and mailbox formats remain **1**, Postgres schema **4**, and
storage wire **6**. Existing histories and JSON metadata survive. Arbitrary
YAML metadata and libpq-specific connection settings have explicit migration
or refusal rules: read `neosian docs migration` before upgrading v1 homes
and client registrations. Error identifiers remain reserved forever.

`neosian docs` lists every embedded page; `neosian docs TOPIC` reads it.
The versioned `neosian-docs.json` release artifact supplies those same pages
to the website. `neosian-contracts.tar.gz` contains machine contracts,
fixtures, the independent HTTP conformance runner and the optional one-time
metadata converter. `neosian schema state`, `wire`, `errors`, `profile` and
`grants` print the
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
an optional one-time migration script. Node checks exercise the shipped Pi
and OpenCode adapters.

Contributions use DCO sign-off (`git commit -s`) and Apache-2.0 licensing.
See [SERVICES.md](SERVICES.md) for environment settings and release accounts,
[SECURITY.md](SECURITY.md) for vulnerability reports, and
[llms.txt](../llms.txt) for the machine-readable front door.
