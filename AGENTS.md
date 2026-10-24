# neosian — conventions for agents and people

The memory/context core for the agent you already use. One Rust binary
provides CLI, MCP and HTTP interfaces; there is no dedicated SDK, Rust
embedding API, Python distribution or model runtime. Read `docs/README.md`
first; `neosian docs <topic>` prints the embedded pages. Python is test,
development and optional one-time migration tooling only (S0, DESIGN §40).

## Hard rules

- **State, not an agent runtime.** Memory, skills, history, mailbox, hooks
  and storage belong here. Hosts own models, orchestration, accounts,
  hosting and business logic. Never import or assume a host's internals.
- **Prompts are data.** Shipped tool descriptions and agent instructions
  live in `assets/` YAML or markdown with frontmatter, never Rust strings.
- **Money = integer micro-USD** with ceiling division; floats only at
  display edges. **Timestamps are timezone-aware UTC.** Preserve historical
  token classes and data even though the product no longer meters models.
- **The wire is the storage contract.** Custom backends are independent
  HTTP services verified by the shipped conformance runner. The core never
  applies Postgres DDL on connection; migrations are operator-owned.
- **Seams are frozen; error codes are append-only.** Scope/actor grammar,
  format 1, wire 6, Postgres schema 4 and every reserved code change only
  by a recorded decision, never in passing. Retired runtime event names
  are reserved; they are not silently repurposed for state notifications.
- **Preserve data.** Versions, actors, timestamps, opaque JSON metadata and
  erasure skeletons survive supported operations. Refuse unsupported
  formats explicitly; do not silently migrate or discard history.
- **Profiles select; backends authorize.** Portable profile format 1 excludes
  local bindings and credentials. New profile capture defaults off; local/shared
  capture requires matching binding consent and filtering before persistence.
  Server grants intersect token allowances before reads or writes; they never
  turn the operator's hosted MCP mounts into a per-client surface.
- **Search is a read contract.** Memory-search version 1 is an optional wire-6
  capability, distinct from history search. Authorize before scanning/paging;
  exclude erased content and lifecycle records. Unicode 17.0.0 matching and
  opaque cursor semantics are shared across backends, never database defaults.
- **Knowledge imports are explicit.** Preview scoped Markdown/bundle changes;
  refuse lifecycle records and erased destinations. Collection rules validate
  imports and explicit checks, not authority. Structure/provenance stays in
  versioned content. Apply/export requires paused writers; full archives retain
  the separate history/envelope restoration contract.

- **Skill revisions remain state.** Dependencies and text resources are checked
  through mounted authority; no automatic install or execution. Profile pins
  guard current source/version/hash and refuse changed or erased content.
  Preserve legacy `eo` as edit-only; selection never grants broader access.

## Conventions

- Rust uses `rust-toolchain.toml`'s exact pin, repeated by `rust-version`.
  `cargo fmt`, `cargo clippy -D warnings` and `cargo deny` pass.
- Python tooling passes `mypy --strict`, `ruff` and `black` (88 columns).
  Constants stay module-local. Keep code small and purposeful.
- File-size gate: warn 300, fail 500 lines over `crates/**/*.rs`.
  Allowlist entries need a ceiling and reason; stale entries fail.
- Tests need no model keys. Rust unit/integration tests and independent
  raw CLI/MCP/HTTP contracts are the default gates. Literal fixtures and
  frozen v1 reference expectations must never be regenerated from Rust.
  Postgres tests explicitly require a disposable test DSN. External HTTP
  conformance operates only on unique test scopes and conversations.
- The canonical assets are `assets/`. A staged crate snapshot is generated,
  never authored. `llms.txt` and `assets/llms.txt` are byte-identical twins.
  Every docs page appears exactly once in `assets/docs/topics.txt`; the
  build and contract gates enforce the manifest and embedded bytes.

## Gates

| Command | What |
|---|---|
| `make install` | `uv sync --locked --all-groups`, development tools only |
| `make lint` / `make format` | ruff + black --check / autofix |
| `make typecheck` | `mypy --strict tests scripts` |
| `make test` | build the binary and run independent contracts/tooling, keyless |
| `make size` | Rust source size gate (300/500) |
| `make rust-lint` / `make rust-test` | fmt, clippy, deny, generated release workflow / locked Cargo tests |
| `make test-postgres` | wire and physical-row checks; requires `NEOSIAN_TEST_POSTGRES_DSN` |
| `make test-storage-contract [backend=http] [profile=full]` | independent custom-storage conformance |
| `make test-container` | image conformance on files and Postgres |
| `make bench-hooks [home=DIR]` | p50 of 20 warm runs per event; a number over budget fails |
| `make docs-artifacts` | deterministic versioned docs and contracts release artifacts |

All six default gates (lint, typecheck, test, size, rust-lint, rust-test)
pass with no model API keys before every commit. `docs/SERVICES.md` lists
runtime settings, test infrastructure and publishing accounts. CI also
checks the six client integrations, image, Postgres, packages and latency.

## Versioning and releases

The version authority is `Cargo.toml` `[workspace.package].version`.
Release axis = annotated `v<X.Y.Z>` tags, cut by `make release v=X.Y.Z`
on a clean tree with the version's `## [X.Y.Z]` section in
`docs/CHANGELOG.md`. Bump on public-surface change, not per commit.
`[Unreleased]` accumulates until that bump.

Pushing a `v*` tag runs cargo-dist's generated `release.yml` for binaries,
then `publish-core.yml` for checksummed docs/contracts and GHCR, and
`publish-channels.yml` for npm/crates.io. No PyPI product is published.
Homebrew, signing and notarization remain deferred. The site's `RELEASE`
pin moves only after publication; a phase close does not deploy it.

## Commits

- Subject ≤ 72 chars, outcome not mechanics: `<what became true>`, prefixed
  by the phase id, or `meta:` for housekeeping.
- Body when useful: the why; `Docs: <section>` for decisions;
  `Verified: <how>` (or explicitly `not verified`).
- Sign off every commit (`git commit -s`, DCO). No attribution trailers.
  One commit per coherent outcome. Push `--follow-tags` only when requested.
- `record/` is ignored local planning history. Edit it in place, never
  force-add or commit it.
