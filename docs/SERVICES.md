# Services and environment

Neosian 2 runs locally without a model provider, account or hosted service.
The product is one Rust binary. Python 3.12+ and uv are development and
optional migration tools; no Python distribution or provider SDK is installed.
The exact Rust toolchain is pinned in `rust-toolchain.toml`.

## Runtime settings

| Setting | Purpose when set | When absent |
|---|---|---|
| `NEOSIAN_HOME` | FileStore home; a leading `~` expands | `~/.neosian`; an explicit `--root` takes precedence |
| `NEOSIAN_SCOPE` | Default mount/scope for commands and integrations | Scope derives from the local login and working directory |
| `NEOSIAN_PROFILE` | Portable profile selection, below explicit `--profile` | Longest registered project root, else legacy selection |
| `NEOSIAN_POSTGRES_DSN` | Explicit Postgres URI or keyword/value DSN | Use files unless `--url` selects a remote service |
| `PGSSLROOTCERT` | PEM trust bundle for direct Postgres TLS | Built-in public roots; certificates and hostnames are verified |
| `NEOSIAN_CLIENT_TOKEN` | Bearer token for `--url` | Remote operations refuse without a token |
| `NEOSIAN_SERVE_TOKEN` | One bearer token or a per-client allowance table | `serve` refuses to start |
| `NEOSIAN_SERVE_GRANTS` | Path to operator-owned exact-scope/conversation grants TOML, loaded at startup | Legacy token allowances only; profile HTTP bindings refuse this mode |
| `SSL_CERT_FILE` | Additional PEM trust bundle for HTTPS clients | Built-in public roots |
| `NEOSIAN_INSTALL=container` | Container installation identity | Installation shape derives from the executable |
| `NO_COLOR` | Disable terminal colour | Normal terminal detection |

Flags and environment values containing credentials are never copied into
client registrations or hook commands. Supply credentials in the client's
own environment. `status` reports installation paths and registrations,
not secrets. Old provider credential tables in `config.toml` are preserved
but ignored by the core.

Client configuration follows each client's native locations and overrides:
`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `OPENCODE_CONFIG_DIR`,
`PI_CODING_AGENT_DIR`, `XDG_CONFIG_HOME`, and Windows `APPDATA` /
`LOCALAPPDATA`. See `neosian docs agents`; `setup` previews before `--write`.
Login/home variables describe local defaults, not cross-machine identity.

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

The six default gates need no model API keys. `make test` runs literal
contracts and frozen v1 reference cases through the built binary. Rust's
unit and integration tests run separately with `make rust-test`. Node 24
runs `tests/clients/pi_record.mjs` and `tests/clients/mailbox.mjs` for the
shipped client adapters.

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
  the independent runner and optional metadata converter. `GITHUB_TOKEN`
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

Memory-search version 1 introduces no service setting or model dependency.
The storage runner checks it when advertised on either full/base profiles;
reference Postgres and container gates include these cases. Scope grants
apply before search, and restart-based grant revocation also applies to
resumed search pages. Search has no separate cache or index to provision.

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
