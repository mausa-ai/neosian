# neosian — conventions for agents and people

The memory/context core for the agent you already use. One Rust binary
provides CLI, MCP and HTTP interfaces; there is no dedicated SDK, Rust
embedding API, Python distribution or model runtime. Read `docs/README.md`
first; `neosian docs <topic>` prints the embedded pages. Python is for
development and tests only (DESIGN §40).

## Hard rules

- **State, not an agent runtime.** Memory, skills, history, mailbox, hooks
  and storage belong here. Hosts own models, orchestration, accounts,
  hosting and business logic. Never import or assume a host's internals.
  On an agent's explicit call and only when enabled, the `harness` tool
  starts another installed harness's own headless command; neosian holds no
  model key, chooses no provider, runs no loop of its own and schedules
  nothing. A hook may start a short-lived lander that sends closed turns to
  the configured store and exits when nothing is landable or after ten
  minutes; at most two run per home, and the next attempt waits for a hook
  event.
- **Prompts are data.** Shipped tool descriptions and agent instructions
  live in `assets/` YAML or markdown with frontmatter, never Rust strings.
- **Money = integer micro-USD** with ceiling division; floats only at
  display edges. **Timestamps are timezone-aware UTC.** Preserve historical
  token classes and data even though the product no longer meters models.
- **The wire is the storage contract.** Custom backends are independent
  HTTP services verified by the shipped conformance runner. The core never
  applies Postgres DDL on connection; migrations are operator-owned.
- **Seams are frozen; error codes are append-only.** Scope/actor grammar,
  format 1, turn format 2, wire 6, Postgres schema 4, records extensions 1 and 2
  and every reserved code change only by a recorded decision, never in
  passing. Retired runtime event names
  are reserved; they are not silently repurposed for state notifications.
- **Preserve data.** Versions, actors, timestamps, opaque JSON metadata and
  erasure skeletons survive supported operations. Refuse unsupported
  formats explicitly; do not silently migrate or discard history.
- **Reviewed upkeep.** Proposal digests bind content, never grant authority.
  Preserve sources and require current mounted/server permissions. Apply and
  recovery require paused writers; report partial receipts and inspect lost
  responses before fresh proposals. Keep lifecycle and erased records protected.
- **Profiles select; backends authorize.** Portable profile format 1 excludes
  local bindings and credentials. New profile capture defaults off; local/shared
  capture requires matching binding consent and filtering before persistence.
  Machine login names the default host through its discovery URL. It may install
  shared text capture after displaying the host notice. A local capture-off
  preference or host lock keeps managed recording off through profile refresh.
  Server grants intersect token allowances before reads or writes; they never
  turn the operator's hosted MCP mounts into a per-client surface.
- **Search is a read contract.** Memory-search version 1 is an optional wire-6
  capability, distinct from history search. Authorize before scanning/paging;
  exclude erased content and lifecycle records. Unicode 17.0.0 matching and
  opaque cursor semantics are shared across backends, never database defaults.
  Memory ranking version 1 pins its scoring arithmetic in the shared matcher;
  statistics stay inside one authorized scope and never need a model or index.
  History ranking version 1 reuses it over candidate turns only; requests
  without its fields stay version 1, and agent-door fallbacks are stated.
  Ranked search keeps facts per candidate, never content, and reads only the
  winners again inside the same lock or snapshot; past
  `NEOSIAN_SEARCH_MAX_CANDIDATES` it refuses, never truncates.
- **Answers are spooled; pages may be bounded by size.** A large answer is
  written item by item into a spool (memory, then a temp file under
  `NEOSIAN_MAX_TEMP_BYTES`) with unchanged bytes, and sent with its length
  only after the store is released: a slow reader never holds the store, and
  every refusal is the ordinary response. Optional wire-6 `size_paging`
  version 1 bounds history-search and turn-read pages by bytes (at least one
  turn) with opaque continuations that bind the request and confer nothing;
  requests without its fields are unchanged, and the binary pages only after
  a 2.16.0 request is refused for its size. Postgres checks each row alone
  against the response budget; only one record past a budget is refused.
- **Sessions are bounded records.** Optional wire-6 `turn_records` version
  1 stores, moves, reads and searches a turn as records of at most 64 KiB
  of encoded text, written in drafts and restored in parts that stay
  invisible until sealed or committed. Records exist on a FileStore only
  after `neosian records enable` and on Postgres only with operator-installed
  records extension 1 or 2; `append_turn` and every legacy route keep their
  bytes, and format-1 data is read in place. The binary never sends a write
  or restore part over 983,040 bytes. Erasure removes every record, draft
  and digest of the turns it covers.
- **Knowledge imports are explicit.** Preview scoped Markdown/bundle changes;
  refuse lifecycle records and erased destinations. Collection rules validate
  imports and explicit checks, not authority. Structure/provenance stays in
  versioned content. Apply/export requires paused writers; full archives retain
  the separate history/envelope restoration contract.

- **Skill revisions remain state.** Dependencies and text resources are checked
  through mounted authority; no automatic install or execution. Profile pins
  guard current source/version/hash and refuse changed or erased content.
  Preserve legacy `eo` as edit-only; selection never grants broader access.
- **Review is content state.** Governance labels and hashes are assertions,
  never authenticated roles. Profile policies narrow knowledge retrieval;
  grants authorize every access path. Raw reads/history remain inspection
  surfaces. Expiry never deletes; body erasure includes review metadata.

- **Changes are durable references.** Advertise change-feed v1 only when mutations
  and events recover together. Reauthorize each page, expose reset/retention gaps,
  and keep content out of events. Postgres extension 1 is operator-installed on
  schema 4 with paused/restarted writers. Cursors and mailbox references never
  grant access or execute agents.

- **Exchange is explicit and one-way.** Current-memory snapshots/deltas select
  one authorized tracked scope and an explicit local replica. Pause source
  writers; apply data and checkpoint together. Refuse local drift, stale
  ordering and missing receipts. Gaps need a fresh empty replica. Exclude raw
  turns, lifecycle records and source history bodies; hashes are not authority.

- **Native capture is explicit.** Machine-local registrations select host Markdown,
  project, client and destination. Check mounted authority before reading sources.
  Local copies and receipts commit together; never revive erased/deleted copies or
  overwrite independent edits. Profiles and remote stores require their own future
  capture contract. Source files stay intact; hook guidance never executes an agent.

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
| `make bench-memory [backends=file,postgres]` | peak RSS per request (searches, answer-sized and size-paged reads) over an adversarial corpus, and per record operation by size; over budget, a rise past 2 MiB or a 16 MiB seal over twice the 1 MiB seal fails |
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
