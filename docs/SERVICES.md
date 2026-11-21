# Services and environment

Neosian 2 runs locally without a model provider, account or hosted service.
The product is one Rust binary. Python 3.12+ and uv are development and
test tools only; no Python distribution or provider SDK is installed.
The exact Rust toolchain is pinned in `rust-toolchain.toml`.

## Runtime settings

| Setting | Purpose when set | When absent |
|---|---|---|
| `NEOSIAN_HOME` | FileStore home; a leading `~` expands | `~/.neosian`; an explicit `--root` takes precedence |
| `NEOSIAN_LOGIN_TOKEN` | Opaque machine credential for headless login and managed profile access | Interactive login uses its private credential |
| `NEOSIAN_SCOPE` | Default mount/scope for commands and integrations | Scope derives from the local login and working directory |
| `NEOSIAN_PROFILE` | Portable profile selection, below explicit `--profile` | Longest registered project root, then managed default, else legacy selection |
| `NEOSIAN_POSTGRES_DSN` | Explicit Postgres URI or keyword/value DSN | Use files unless `--url` selects a remote service |
| `PGSSLROOTCERT` | PEM trust bundle for direct Postgres TLS | Built-in public roots; certificates and hostnames are verified |
| `NEOSIAN_CLIENT_TOKEN` | Bearer token for `--url` | Remote operations refuse without a token |
| `NEOSIAN_SERVE_TOKEN` | One bearer token or a per-client allowance table | `serve` refuses to start |
| `NEOSIAN_SERVE_GRANTS` | Path to operator-owned exact-scope/conversation grants TOML, loaded at startup | Legacy token allowances only; profile HTTP bindings refuse this mode |
| `SSL_CERT_FILE` | Additional PEM trust bundle for HTTPS clients | Built-in public roots |
| `NEOSIAN_INSTALL=container` | Container installation identity | Installation shape derives from the executable |
| `NO_COLOR` | Disable terminal colour | Normal terminal detection |

Bulk operation budgets are configured with `NEOSIAN_KNOWLEDGE_MAX_DOCUMENTS`
(default 10,000; maximum 100,000), `NEOSIAN_KNOWLEDGE_MAX_BYTES` (256 MiB; 2 GiB),
`NEOSIAN_MAX_TEMP_BYTES` (512 MiB; 8 GiB), which also bounds the part of one
served answer `neosian serve` spools to disk, `NEOSIAN_REPLICA_MAX_PATHS` (100,000;
1,000,000), `NEOSIAN_REPLICA_MAX_BYTES` (1 GiB; 64 GiB), `NEOSIAN_MAX_FILE_BYTES`
(64 MiB; 1 GiB), and `NEOSIAN_MAX_RESPONSE_BYTES` (64 MiB; 1 GiB), which bounds
each HTTP storage response and, on Postgres, each row of every read on its own.
Byte settings are positive decimal byte counts, not unit strings. Empty values
use defaults. An answer past a server's spool budget is refused, and the
binary then asks again in smaller pieces (size pages for searches and turn
reads, smaller pages for memory listings), so what 2.16.0 answered is
answered. The one refusal that remains is a single record larger than a
budget (one Postgres row past `NEOSIAN_MAX_RESPONSE_BYTES`, or one item past
the spool budget), for which no smaller answer exists, and a projection page
past the spool budget, which wire 6 cannot split; raise the setting for
either. Through the wire every request is at most 64 MiB, so such a record
comes only from local writes, restores, direct SQL or a lowered setting
(`neosian docs stores`).
`NEOSIAN_SEARCH_MAX_CANDIDATES` (100,000; 1,000,000) is the largest number of
turns or documents holding a term that one relevance search, history or
memory, considers; past it the search refuses and asks for narrower words or
a time range. An operator serving several people under a memory limit can
lower it to promise a ceiling. Each candidate costs 40 bytes plus 4 per query
term (up to twice that while the arrays grow) and the name it is read back
by: one copy of its conversation's id per conversation (at most 128
characters) for history, its path (at most 512 characters) for memory. At the
default, a history search over 100,000 one-turn conversations with
128-character ids peaks at about 33 MiB on files and 43 MiB on Postgres, and a
memory search over 100,000 documents with 500-character paths at about 67 MiB;
the maximum costs roughly ten times that.
`NEOSIAN_MAX_PAGE_BYTES` (8 MiB; 1 GiB) is the largest page a size-paged
request to `neosian serve` is served: a client asking for more gets pages of
this size, so it caps how much one page request transfers. Every answer is
spooled and sent after the store is released, whether or not a client pages;
the binary's own remote client pages only after an unpaged request is
refused for its size (never after the candidate bound).

`neosian serve` needs a writable, disk-backed temporary directory (`TMPDIR`
on Unix, `TMP` on Windows)
for answers over 1 MiB. A memory-backed one (tmpfs) counts against a
container's memory limit and defeats the bound; the published image's `/tmp`
is on its writable layer, so with a read-only root filesystem mount a disk
volume there, not a tmpfs. A server that cannot spool says so once at startup
and holds those answers in memory, as 2.16.0 did, up to
`NEOSIAN_MAX_RESPONSE_BYTES` each; past that the answer is refused with
`value_error`, which the binary answers in size pages. A spool that fails
midway (a full disk) is finished the same way, never a `500` or a cut body.
Disk use is the answers being sent at once, each within
`NEOSIAN_MAX_TEMP_BYTES`, with no total: only the process's open-file limit
bounds concurrent sends (a socket and a spool file each), and no timeout ends
a stalled one. Each server's spools sit under its own locked
`.neosian-serve-<uuid>` directory, removed when it stops. On Unix, the next
server started with the same `TMPDIR` removes an ended server's spools after
checking directory ownership and the lock. A crash before startup publishes
the lock can leave an empty directory or marker. On Windows, use a private
temporary directory and remove a killed server's directory after it exits.

What a served read holds in the server: turn reads and searches about one
turn, `memory/read` its document, `memory/versions` and `memory/history` their
page of up to 500 versions with content (and, on files, the document's whole
journal or the scope's whole history for every page, as 2.16.0 did: 320 MiB
for a history page over 1,500 versions of 100 KB), `conversation/read_projections`
its page of entries, and the hosted `/mcp` `continue_session` the whole
conversation it continues (142 MiB for 130 MB of turns). A listing asked for
`limit: 0` reads no rows.
See `neosian docs stores` for each setting's exact scope; these do not change
permissions, skill/context limits or the format-1 exchange bundle bounds.

`config.toml` in the neosian home also holds `[harness]`: `enabled` (default
false), `turn_seconds` (1800; 10 to 86400), `max_children` (2; 1 to 8) and
`directories` (absolute paths a child may work in besides the server's own).
A value out of range or of the wrong type keeps the tool off. The tool finds
`claude`, `codex` and `opencode` on absolute entries of the MCP server's
`PATH`; children inherit the server's environment plus
`NEOSIAN_HARNESS_CHILD`, `PWD`, `NEOSIAN_HARNESS_PARENT` and
`NEOSIAN_HARNESS_SESSION` (which the record hook reads), Codex children a
private `TMPDIR`, OpenCode children `OPENCODE_CONFIG_CONTENT` and, in
read-only, `OPENCODE_DISABLE_PROJECT_CONFIG`. See `neosian docs harness`.

Flags and environment values containing credentials are never copied into
client registrations or hook commands. Supply credentials in the client's
own environment. `status` reports installation paths and registrations,
not secrets. Old provider credential tables in `config.toml` are preserved
but ignored by the core.

`setup --check` inspects registrations and selected storage/profile configuration
using this shell's environment. Its subprocess probe clears inherited credentials
and store selectors, uses a private temporary FileStore, then removes it. No model
keys or services are needed for the probe. Configured source connectivity and
permissions are not tested, and client connection/hook delivery remain unobserved.

Client configuration follows each client's native locations and overrides:
`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `OPENCODE_CONFIG_DIR`,
`PI_CODING_AGENT_DIR`, `XDG_CONFIG_HOME`, and Windows `APPDATA` /
`LOCALAPPDATA`. See `neosian docs agents`; `setup` previews before `--write`.
Login/home variables describe local defaults, not cross-machine identity.

Claude Code setup uses its public plugin CLI to install a bundled local mod.
Claude owns that mod's timers, model requests, authentication and UI. No model
provider or daemon is added to Neosian's storage/MCP process. Its statusline
bridge stores only bounded cache observations under `neosian-cache/observations`
beside the selected Claude settings. `--claude-cache off` disables it and
restores an owned statusline. See `neosian docs agents` for session controls,
TTL requirements, compatibility and the limits of cache estimates.
On Windows, statusline delegation honors Claude's `CLAUDE_CODE_GIT_BASH_PATH`,
finds standard Git Bash locations, and otherwise uses PowerShell.

Portable profiles use `$NEOSIAN_HOME/profiles/` and machine-local
`profile-bindings.toml`. Bindings reference arbitrary uppercase environment
variable names for HTTP tokens and Postgres DSNs; values are read only at use.
Setup reports and forwards the names without copying values. Profiles default
to capture off; enabling local/shared capture requires the matching binding's
consent. `profile-spool/` holds filtered pending spans and policy fingerprints.
The profiles page documents recovery after a policy or destination change.

`serve` binds loopback by default. Terminate public TLS at an operator-owned
proxy and pass bearer authentication through. A bare server token has the
whole-store allowance; per-client tables can restrict scope and conversation
prefixes. `neosian docs wire` documents their grammar and capability gates.
No mounts means no `/mcp` surface. Storage routes remain available.
With grants enabled, absent permissions deny access and exact-scope grants
intersect legacy allowances. All `/mcp` access is refused in that mode; use a
local profile MCP bridge. Restart after token/grant changes. An operator grant
permits whole-store enumeration/restore and must remain administrator-only.

Knowledge review policies need no account service or background scheduler.
Optional profile mount `knowledge_policy` selects all/effective/approved content;
the server advertises `knowledge_policy: {version: 1}` for filtered memory search.
This does not restrict raw reads or establish reviewer identity. Hosts control
approval credentials/workflows; restart the server to apply grant revocation.

Postgres schema creation is an explicit operator action:

```sh
neosian schema postgres --schema neosian > schema.sql
psql "$NEOSIAN_POSTGRES_DSN" -f schema.sql
```

The binary never applies DDL on connection. Schema generation remains 4.
DSNs use tokio-postgres syntax, not libpq defaults or service files. Supported
TLS modes are `disable`, `prefer` and `require`; TLS always verifies the peer.
Use `require` plus `PGSSLROOTCERT` when migrating a private-CA `verify-full`
connection. Mutual TLS and libpq-only DSN options are refused. See
`neosian docs migration` before upgrading an existing database connection.

## Test infrastructure

Native memory capture adds no service or host account. Local registrations in
`<NEOSIAN_HOME>/native-memory.json` authorize selected Claude Code/Codex Markdown
sources; existing record hooks perform bounded scans. Capture and private
FileStore receipts recover together. Upgrade all writers before enabling it.
No source is registered by setup, and profiles/remote destinations remain
unsupported. See `neosian docs native-memory`.

The six default gates need no model API keys. `make test` runs literal
contracts and frozen v1 reference cases through the built binary. Rust's
unit and integration tests run separately with `make rust-test`. Node 24
runs `tests/clients/pi_record.mjs` and `tests/clients/mailbox.mjs` for the
shipped client adapters. `node --test tests/clients/claude_cache.mjs` exercises
the cache adapter without keys or Claude installed. With Claude Code 2.1.314+,
copy `assets/clients/claude-cache` to a temporary directory, copy
`tests/clients/claude-cache` into its `tests` subdirectory, then run
`claude plugin validate DIR` and `claude plugin test DIR`. These host tests
mock model requests and need no keys. Real-time cache tests use only explicitly
authorized disposable sessions and are separate from the keyless gates.

| Setting | Use |
|---|---|
| `NEOSIAN_BINARY` | Binary under test; defaults to `target/debug/neosian` in the contract tier |
| `NEOSIAN_TEST_POSTGRES_DSN` | Explicit disposable database for `make test-postgres`; selecting that tier without a DSN fails |
| `NEOSIAN_TEST_SERVER_URL` / `NEOSIAN_TEST_SERVER_TOKEN` | Disposable custom HTTP service for `make test-storage-contract backend=http` |
| `NEOSIAN_TEST_SERVER_PEER_URL` | Optional second worker on that service's same storage and bearer identity |
| `NEOSIAN_CONTAINER_IMAGE` / `NEOSIAN_CONTAINER_PORT` | Test image/tag and loopback port; defaults `neosian:conformance` / `16367` |
| `NEOSIAN_CONTAINER_TMPDIR` | Parent directory the Docker daemon can volume-mount |
| `NEOSIAN_CONTAINER_POSTGRES_DSN` | Container-reachable test DSN when host-address rewriting is unsuitable |
| `NEOSIAN_VERIFY_ROOT` | Release source checkout used by a newer release verifier |

For a local disposable Postgres service:

```sh
docker run --rm -d --name neosian-postgres-test \
  -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:5433:5432 postgres:17
NEOSIAN_TEST_POSTGRES_DSN=postgresql://postgres:postgres@127.0.0.1:5433/postgres make test-postgres
```

Fixtures create and drop only their uniquely named schemas. `make
test-container` builds the image, runs the independent wire suite against
FileStore, then Postgres when a test DSN is supplied. Both legs are required
in CI. External HTTP tests use unique scopes and conversation IDs and leave
history behind; the service owner disposes of that test storage. Full/base
capability profiles and runner instructions are in
[the conformance guide](../tests/contract/storage/README.md).

`make bench-hooks` measures 20 warm runs per event against a literal 50 MB
home. `home=DIR` copies an existing home before benchmarking. The release
binary must meet the documented p50 budgets; no provider call is involved.

`make bench-memory` is the memory gate: it starts the release `neosian serve`
once per request over a literal adversarial corpus (600 turns and 600
documents of 64 KiB, 20,000 turns and 20,000 documents of 1 KiB, a one-letter
term every item holds) and fails when a request's peak resident memory passes
its budget (32 MiB including startup). A ranked search with three passages
over a 1 MiB release notebook exercises frequent matches and Markdown headings
under the same budget. Answer-sized requests are gated too:
history search at limit 500 over 64 KiB turns (a 32 MiB answer), a 500-turn
page of turns, and reads that follow size pages to the end of a conversation
(64 KiB pages over both conversations, 1 MiB pages of a 500-turn search). Two
spread corpora at the default candidate bound have budgets of their own, set
from measurement plus 30%: 100,000 one-turn conversations with 128-character
ids (56 MiB) and 100,000 documents with 500-character paths (88 MiB).
`backends=file,postgres` adds the Postgres leg, which needs
`NEOSIAN_TEST_POSTGRES_DSN` and creates and drops its own schema; CI runs both.

`scripts/check_codex_skills.py` verifies setup, native skill discovery, MCP
workflow loading (including profiles), removal and re-enable with the official
Codex CLI. CI pins `@openai/codex` 0.166.0. The check uses disposable homes,
starts no model turn, and requires no model account or API keys.

## Release accounts and workflow

Cargo's workspace version is authoritative. A signed-off clean commit with
its `docs/CHANGELOG.md` version section can receive an annotated `vX.Y.Z`
tag through `make release v=X.Y.Z`. Pushing the tag runs cargo-dist's
`release.yml`, followed by `publish-core.yml` and `publish-channels.yml`.
A release is complete only when every publication job succeeds.

- **GitHub Releases:** five native targets (Linux x86-64/ARM64, macOS
  x86-64/ARM64, Windows x86-64), checksums, installers, plus
  `neosian-docs.json` and `neosian-contracts.tar.gz` with SHA-256 sidecars.
  The docs artifact is the website's v2 input. The contracts archive carries
  the independent runner and fixtures. `GITHUB_TOKEN`
  needs `contents: write` in the publishing jobs.
- **GHCR:** `ghcr.io/mausa-ai/neosian`, Linux amd64/arm64 native builds and
  a multi-architecture manifest. `GITHUB_TOKEN` needs `packages: write`.
  Tags are the release version and `latest` for stable releases only.
- **npm:** package `neosian`; trusted publisher owner `mausa-ai`, repository
  `neosian`, calling workflow `release.yml`, environment `npm`. Publication
  uses Node 24/npm with OIDC and no permanent npm token.
- **crates.io:** package `neosian`, environment `crates-io`. Trusted
  publishing names the same repository and calling `release.yml` workflow.
  First publication requires a verified account email and a scoped bootstrap
  `CARGO_REGISTRY_TOKEN` if trusted publishing cannot yet be configured;
  remove that token after configuring OIDC. The workflow supports that
  bootstrap explicitly. Source packages embed canonical assets and build
  without a repository checkout.

Python/PyPI publication and provider-service secrets are retired in v2.
Existing v1 artifacts remain historical downloads, without a maintained v1
line. No release job needs model keys. Homebrew, platform signing and Apple
notarization remain deferred.

Channel retries compare the existing immutable package digest before
skipping an already published version; a different digest fails. Rehearsal
scripts verify local native shell/npm installs and source-package builds.
The separately dispatched live-release workflow verifies published archives,
Windows MSI installation/uninstallation and fresh registry installations.
Neither a local S0 close nor its done-tag publishes or deploys anything.

The site repository's `RELEASE` pin advances only after publication. Its
build verifies the docs artifact checksum and renders the exact release's
pages; authored documentation stays in this repository's `assets/docs/`.

Memory-search version 1, memory ranking version 1, memory passages version
1, history ranking version 1 and size paging version 1 introduce no service or
model dependency; `NEOSIAN_SEARCH_MAX_CANDIDATES` bounds their relevance
candidate sets and `NEOSIAN_MAX_PAGE_BYTES` caps size pages. The storage runner checks each when advertised
on either full/base profiles;
reference Postgres and container gates include these cases. Scope and
conversation grants apply before search, and restart-based grant revocation
also applies to resumed search pages. Search has no separate cache or index
to provision, and history ranking needs no Postgres DDL or extension.

Knowledge import/collection/validate/references/export introduces no service
setting, account or dependency. It uses existing mounted storage access and
explicit local input/output paths. Apply and export require paused writers;
collection rules are optional validation rather than server authorization.

S4 skill management uses existing storage/profile settings and needs no model
keys, package registry or external execution service. Skill directory packages
are UTF-8 data; source commands/scripts are never executed during import/load.

Reviewed upkeep needs no additional service, credential, database object or
model account. CLI/MCP compose existing mounted storage operations. Hosts may
supply reasoning, scheduling and approval policy. Pause other writers for apply
and recovery; inspect partial or lost-response outcomes before fresh proposals.
The default contracts cover file/HTTP guards and fault injection; the Postgres
tier checks preserved physical rows, revisions and erasure.

## Change-feed storage extension

FileStore has no new runtime setting: cooperating memory writers persist private
redo plans and reference-only events. Stop older writers before upgrading.
Postgres schema 4 can operate without the optional feed. To enable extension 1,
pause writers, review `neosian schema changes-postgres --schema NAME`, apply with
the schema owner's connection, and restart all compatible writers. No DDL runs
on connection. The core owns no automatic retention schedule; operators preview
and apply bounded `changes prune` requests. See `neosian docs changes`.

The independent file and Postgres conformance fixtures enable the optional feed;
physical Postgres tests also verify compatibility without it, refusal of partial
or future extensions, and rollback when events cannot be recorded. Container
conformance installs the extension only in its disposable owned schema.

## Incremental exchange

Exchange adds no account, environment setting, scheduler or service. Sources
use existing file/Postgres/HTTP selection and current read grants; Postgres needs
changes extension 1. Export requires paused compatible writers and `--paused`.
Receivers require explicit local roots/scopes and `--write` after preview.
The independent suite covers replay, lost checkpoints, process-kill recovery,
concurrent apply, source mutation/revocation and physical erasure; the Postgres
tier covers direct and remote sources. See `neosian docs exchange` for limits.
