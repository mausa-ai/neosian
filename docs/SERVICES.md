# Services & keys

> Companion to [AGENTS.md](../AGENTS.md) (conventions) and, in the
> maintainer's record, DESIGN §10–§11 (harness and CI) and ECOSYSTEM
> (frozen seams).

**Keyless boot is the invariant**: `make lint`, `make typecheck` and
`make test` need **zero third-party accounts**. FakeProvider
(`Provider.FAKE`, `Model.FAKE*` — `neosian.fake`) is the keyless path; the
CI test matrix carries no secrets at all, and that absence is the assertion.

Keys are read from the process environment at client construction (never at
import). `<home>/config.toml` (written by `neosian configure`) fills in for
the shell's verbs, with env vars taking precedence; library code and the
examples read the environment only (ledger #280). The same file's
`[[chat.mcp]]` tables carry literal `env` and `headers` values for the
MCP servers `neosian chat` opens (the file is 0600; the shell's alone,
ledger #282). A provider is
available exactly when its key is set; asking for an unavailable provider
raises `MissingAPIKeyError` (`agent_missing_api_key`).

## Optional — each key unlocks a real provider

| Key | Provider | Off means |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic (Claude; vision/PDF, prompt caching, adaptive thinking) | Claude models leave the router; `external_anthropic` self-skips; `chat` and `playground` refuse a Claude model before its SDK (`MissingAPIKeyError` naming the var) |
| `OPENAI_API_KEY` | OpenAI (`gpt-6-astra`, the GPT-6 family including GPT-6.1 Sol; `gpt-5.1` is in its migration window; the Responses API) | GPT models leave the router; `external_openai` self-skips; `chat` and `playground` refuse a GPT model before its SDK (`MissingAPIKeyError` naming the var) |
| `CEREBRAS_API_KEY` | Cerebras (default provider; `gpt-oss-120b`, `qwen-3.8-27b` — shipped door rows on the OpenAI wire since #218) | The rows stay listed but their first call raises `MissingAPIKeyError` naming the var; `external_cerebras` self-skips |
| `XAI_API_KEY` | xAI (`grok-4.6`, a shipped door row — `Model.GROK_4_6`, DESIGN §31; the Responses API) | The row stays listed but its first call raises `MissingAPIKeyError` naming the var; `external_xai` self-skips |
| `GEMINI_API_KEY` | Google Gemini API, OpenAI-compatible endpoint (`gemini-3.8-flash` and `gemini-3.7-flash`, shipped door rows) | As above; `external_gemini` self-skips |
| `MOONSHOT_API_KEY` | Moonshot (`kimi-k3`, a shipped door row: `Model.KIMI_K3`) | As above; `external_kimi` self-skips |
| `DASHSCOPE_API_KEY` | Alibaba Model Studio, Singapore, the token plan (`qwen3.8-max`, a shipped door row: `Model.QWEN_3_8_MAX`; the reasoning echo and the `enable_thinking` switch) | As above; `external_qwen` self-skips |

With **no** keys set, the library still imports, constructs, runs (on
FakeProvider), and passes its full default test tier.

A registered door (`register_model`, DESIGN §19) reads its own key from the
env var the door names — `OpenAICompatible(api_key_env=...)` — env only,
never argv; a missing one fails at the first call naming it, and guardrails
on a door need it at construction. A keyless door (`api_key_env=None`, a
local llama-server or Ollama; DESIGN §31.6) reads nothing: the SDK gets a
placeholder in place of a key, never `OPENAI_API_KEY`, and `configure` and
`status` list no row for it. `neosian configure` manages every key:
the shipped providers and the shipped door rows by name (`--provider xai`),
and any other door by the env var its key lives in (`--env ACME_API_KEY
--key -`), since the shell loads no agent file and cannot know a door one
registers. The shell's loader exports every stored key where the
environment has none, so such a door finds its key when its file loads.

### Candidate doors — the external suites only, until a row is green

Nothing in the library reads these. Each drives one lane of NW's gate
(`tests/external/lanes.py`, DESIGN §19.7); off means that suite
self-skips. A green row is promoted into the table above (the way
`XAI_API_KEY` and `GEMINI_API_KEY` were, 2026-09-02, `MOONSHOT_API_KEY`
on 2026-09-15 and `DASHSCOPE_API_KEY` on 2026-09-16); a red one exits
whole (DeepSeek and Alibaba Model Studio did, 2026-09-02, both
re-entered under NW1, ledger #211; DeepSeek left again on 2026-09-16
after its third board, ledger #261 — `baselines.md` keeps every run).
One candidate lane is open, and it is a candidate by construction: a
local row has no card to seal and no fixed endpoint, so it never becomes
a `Model` member (NW2, ledger #285); its credential is a URL, not a key.

| Key | Suite | Serving stack |
|---|---|---|
| `NEOSIAN_TEST_LOCAL_URL` | `local` | a llama-server on the OpenAI wire, `gemma-4-e4b-it` (see below) |

Where an account tier caps requests per minute, the lane — shipped or
candidate — is paced on our side (`requests_per_minute` in
`tests/external/lanes.py`, `tests/external/pacing.py`): the lane takes
longer, never runs fewer cells.

## The external suites

`make test-external provider=<openai|anthropic|cerebras|xai|gemini|kimi|qwen|local>`
runs that suite's real-API tests (`-m external_<provider>`).

- `file=<envfile>` routes through `scripts/external_env.py`:
  **value-blind** injection — a per-suite allowlist names exactly which keys
  the suite may see; stdout prints key *names* only, never values. A file
  holding one bare value is taken as the suite's primary key.
- Suites skip **per test** via the `_key_or_skip()` helper — never a
  module-level skip (an empty selection makes pytest exit 5).
- The presence check tests **falsiness**, not `None`: an absent GitHub
  Actions secret arrives as the empty string.

## The Postgres suite — a server, not an API key

`NEOSIAN_TEST_POSTGRES_DSN` points the `external_postgres` suite (the
PostgresStore conformance + concurrency tests, N3) at a live PostgreSQL
server. Unset means every test in the suite self-skips per test via the
same falsiness idiom — `make test` and keyless boot never need a
database. Locally:

```bash
docker run --rm -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:17
export NEOSIAN_TEST_POSTGRES_DSN=postgresql://postgres:postgres@localhost:5432/postgres
cargo build --locked  # Rust server leg, or set NEOSIAN_BINARY
make test-postgres
```

CI supplies the DSN from a service container — never a secret — so the
`postgres` job runs on every push and PR, unlike the dispatch-only
provider jobs (DESIGN §12 ledger #40, #89). Each test creates and drops its
own uniquely-named schema; the server keeps no state between runs. Both
conformance kits, paging, mobility and mixed-worker concurrency run against
the Rust process as well as Python. Rust cases self-skip per test without a
built binary; CI builds it before running this tier.

`NEOSIAN_EXAMPLE_POSTGRES_DSN` is read only by
`examples/fastapi_chatbot.py` — the library itself never reads a DSN
from the environment; the MCP entry point does (below). Unset, the
example app still boots (construction is pure validation) and logs a
warning; requests fail at first pool use.

## The home — `NEOSIAN_HOME`

`NEOSIAN_HOME` moves the home (DESIGN §22): the FileStore every argv
entry point — `neosian memory`, `audit`, `search`, `mcp`, `record`, both
installers, `serve` — and the playground use when `--root`, `--url` and
the DSN are all absent, and where `neosian record` spools. Unset, the
home is `~/.neosian`. An explicit flag always wins; the unit tier points
it at a temporary directory for every test, so no keyless run touches
the real one.

## The update knob — in the config, not the environment

`[update] mode = off | notify | auto` lives in `<home>/config.toml`
(`neosian update --mode M` sets it; default `off`), deliberately not
an environment variable: Python checks PyPI's simple index and the Rust
binary checks the latest stable GitHub Release with an installer asset.
The opt-in authorizes the check (DESIGN §30.3, §39.5). It runs only on
the human door — bare `neosian`, `chat`, `status`, `playground`,
`configure`, on a terminal, never under `--json` — never on `memory`,
`audit`, `search`, `export`, `import`, `record`, `mcp` or `serve`; once per 24 h
by a stamp under the home; silent when offline. The binary's human verbs
are `status` and `configure`. Python uses `update-check`; the binary uses
`binary-update-check`, so each caches its own publication channel.
`NEOSIAN_INSTALL`
(set to `container` by the image) names the installation shape
`status` and `update` report. Python applies only the uv tool shape; the
binary requires the adjacent cargo-dist updater and a receipt matching
its directory and version. Both refuse a new major or a pre-release over
a stable when applying. `--check` never applies unless `--write` is also
explicit; the container only prints its pull command.

The external cargo-dist updater reads its own receipt under
`$XDG_CONFIG_HOME/neosian` or `~/.config/neosian` (Windows:
`$LOCALAPPDATA/neosian`). Its `AXOUPDATER_CONFIG_PATH` and
`AXOUPDATER_CONFIG_WORKING_DIR` overrides select another receipt directory;
the binary checks the same selection before invoking it. The updater's
own network configuration remains cargo-dist's; neosian's release check
needs no token.

## The scope — `NEOSIAN_SCOPE`

`NEOSIAN_SCOPE` is `--scope`'s environment twin (DESIGN §30): read by
the same argv entry points, it names the single read-write mount at
`/memories` when no flag does (`audit` reads it as the raw scope). Unset,
the shell verbs resolve the working directory's project layout (the
`user:<login>` + `user:<login>/proj:<slug>` pair), and a directory with
no derived name refuses at exit 2 (the two doors a client spawns,
`neosian mcp` and `neosian record`, serve the user mount alone there). An
explicit `--scope` or `--mount` always wins. Under a registration that is
once per machine (DESIGN §22.6) this is the per-project override that
needs no second registration: set it in that project's environment for
the client (Claude Code: the `env` block of the project's
`.claude/settings.json`).

Cursor hooks inherit `NEOSIAN_CLIENT_TOKEN`. Its MCP children receive
credential variables through `${env:NAME}` references in the registration,
because Cursor otherwise clears custom MCP environment variables.
Its full recording walkthrough uses the interactive CLI and the existing
Cursor account login; `--print` omits essential lifecycle hooks.

Muse clears custom environment variables for hooks and MCP children.
For remote stores or Postgres, its installers forward the credential by
name: an MCP `${NAME}` reference, and `managed_hooks_env_vars` for the
user-level managed hooks. The client itself must inherit the value.
Project credential-backed hooks are refused (`neosian docs agents`).

## The clients' own homes

`XDG_CONFIG_HOME` (Muse), `CLAUDE_CONFIG_DIR`, `CODEX_HOME` and
`OPENCODE_CONFIG_DIR` are the
clients' variables, not neosian's: the installers, `setup` and `status`
honour each to find that client's config (`~/.claude`, `~/.codex`,
`~/.config/opencode` when unset), so a sandboxed client is registered and
reported where it actually reads. `CLAUDE_PROJECT_DIR` is Claude Code's:
its once-per-machine hook line passes it as `--project`, because a hook's
working directory moves with the agent's `cd` and that variable does not.

`PI_CODING_AGENT_DIR` moves Pi's agent directory (default `~/.pi/agent`),
including its MCP registration and recording extension. Pi inherits
`NEOSIAN_CLIENT_TOKEN` for both subprocesses; no credential is written
into its registration. Pi 1.0.1 or later is required. Its interactive
and print modes both run the extension.

Setup and status also accept `--at CLIENT=DIR` to override a client
configuration directory for one invocation. This takes precedence over the
client variable, is never persisted, and never creates a missing client home.
Use the same override when checking status. `setup --write` asks for client
selection on a terminal; unattended writes require `--yes` or `--client`.

## The MCP server — a DSN, not an API key

`NEOSIAN_POSTGRES_DSN` (renamed from `NEOSIAN_MCP_POSTGRES_DSN` at NA,
ledger #76) is read only by the argv entry points — `python -m
neosian.mcp` / the `neosian mcp` pass-through, `neosian memory` /
`python -m neosian.memory`, and `neosian serve` / `python -m
neosian.server` — never by the library: all are processes configured
through argv, and a DSN must not appear there — argv is world-readable
in `ps` (DESIGN §12 ledger #53). Unset, the entry points use a FileStore
at `--root PATH` or the resolved home; set, they use a `PostgresStore` and
`--root` is refused (an explicit conflict, no precedence rule). None
ever applies the DDL — run `python -m neosian.schemas postgres | psql`
first (DESIGN §8 C1). The Rust binary supports Postgres for `serve`,
`record`, `mcp`, `memory` (except `maintain`), `audit`, `export`, `import`,
`redact` and `prune`. These client commands also reach either server
through `--url`. Binary `search` and `continue` still use local files;
`messages` remains in the Python shell.

Rust uses tokio-postgres's URI or keyword DSNs and four lazy connections.
The driver defaults to `sslmode=prefer`; `sslmode=require` requires TLS,
and every TLS connection verifies the certificate and hostname against
public roots. Set `PGSSLROOTCERT` to a PEM bundle to trust a private CA;
unset means public roots only. Libpq-only DSN options are refused, including
`sslrootcert` within the DSN; use the environment variable for that bundle.
The Python driver keeps its libpq behavior. DSNs and database diagnostics
containing user data are never printed by the Rust server.

An explicit `--url` together with the DSN is an argv conflict, exit 2.

## The state process — bearer tokens, not an API key

`NEOSIAN_SERVE_TOKEN` is read only by `neosian serve` / `python -m
neosian.server`, the binary's `serve` included (R2, DESIGN §39.11).
There is deliberately no `--token` flag: the argv rule
above applies to a token exactly as it does to a DSN (ledger #53). Since
NL (DESIGN §20) the value is a **table** — `actor=token[,actor=token…]`,
each actor in the grammar (`claude-code:laptop=…,app:kit=…`) — or one
bare token, which is the single client `client:default`. The process
records every write under the presenting token's actor
(`<client>[/<what the client said>]`); a malformed table refuses to
start at the grammar tier naming the entry, never the secret.

Since NQ2 an actor may carry an **allowance** — what the token reaches,
not only who it is — written left of the `=` so the token itself stays
opaque to the last character:

```
client:alice@user:alice+alice-=tok1   scopes under `user:alice`,
                                      conversation ids starting `alice-`
client:reader@user:alice=tok2         those scopes; no conversation
client:ops=tok3                        everything, exactly as before
```

Both prefixes are **literal text** matched with `startswith` against the
parameter a request names — neither is interpreted, so `user:alice` also
admits `user:alice2` and an operator who wants the boundary writes
`user:alice/`. An allowance is an **allowlist**: what it does not name is
refused with 403 in the §18 envelope, so `client:reader` above reaches no
conversation at all, and any constrained token is refused `/mcp` (its
reach is the operator's mounts, which no prefix can fence) and the four
`store/*` routes (they move the store whole and restore verbatim under
the archive's own actors). The eraser's routes follow the conversation
rule: `conversation/redact_turns` is fenced by its `conversation_id`,
`conversation/turn_redactions` is checked id by id over its
`conversations` list and refused store-wide. **A token with no allowance
behaves exactly as it did before** — nothing existing changes.

`NEOSIAN_CLIENT_TOKEN` is the **client's** side: read only by the argv
entry points when `--url` names a state process (`neosian memory`,
`neosian record`, `neosian mcp`, `neosian audit`, `neosian search`), never by the library
(`RemoteStore.connect(url, token=…)` takes it explicitly). A distinct
key because one machine runs both; `--url` without it refuses at the
grammar tier. Off means no daemon store from the shell. The Rust binary
reads this key for `record`, `mcp`, `memory` (except `maintain`), `audit`,
`export`, `import`, `redact` and `prune` when they use `--url`. Binary
`search` and `continue` remain local.

`SSL_CERT_FILE` selects a PEM CA bundle for HTTPS clients. The Rust wire
client uses rustls with Mozilla roots when unset, verifies certificates
and hostnames, and replaces those roots with the named bundle when set.
The Python client delegates TLS configuration to httpx. Both clients
use 30-second transport timeouts, follow no redirects and perform no
application retries. Rust transport diagnostics use ureq's wording;
structured storage failures retain the wire's codes and details.

**Off means the process refuses to start** — exit 2 at the grammar tier,
naming the key. Default-deny is code, not configuration (ledger #107,
applied to the wire at #110): the state process never serves
unauthenticated, because TLS terminates at a reverse proxy and an open
port would otherwise expose every scope in the store — an allowance
narrows a token, never a missing one. Clients send
`Authorization: Bearer <token>`; `RemoteStore.connect(url, token=…)`
does it for them and names this key when the server rejects the token.
`/health` is the one unauthenticated route (a container healthcheck
carries no credentials) and reports liveness only.

## The container suite — a URL, not an API key

The `external_server` tier points `RemoteStore` at a **running
state-process container** (NM's done-when; §18.9). Four test keys, all set
by `scripts/container_test.sh` (`make test-container`) — never by hand
in the normal flow:

- `NEOSIAN_TEST_SERVER_URL` + `NEOSIAN_TEST_SERVER_TOKEN` — where the
  container listens and its bearer token. Unset means every test in the
  tier self-skips per test (falsiness).
- `NEOSIAN_TEST_SERVER_ROOT` — the host path of the FileStore leg's
  mounted volume (the substrate-planting seam). Gates the FileStore-leg
  tests.
- `NEOSIAN_TEST_SERVER_SCHEMA` — the serving schema of the Postgres
  leg (with `NEOSIAN_TEST_POSTGRES_DSN` above). Gates the Postgres-leg
  tests.
- `NEOSIAN_CONTAINER_TMPDIR` — where `scripts/container_test.sh` puts
  the FileStore leg's volume (default: `$TMPDIR`, else `/tmp`). Set it
  under your home when the daemon is a VM that mounts only the home
  (Lima), where macOS's `/var/folders` cannot be bind-mounted.
- `NEOSIAN_CONTAINER_POSTGRES_DSN`: optional container-facing address of
  the same test database. Use this when a Docker VM cannot reach the
  host's published port. Unset, the script uses `NEOSIAN_TEST_POSTGRES_DSN`
  with `localhost` or `127.0.0.1` replaced by `host.docker.internal`.

The script runs one leg per backend the image's
`ai.mausa.neosian.backends` label names (R2, §18.9): the FileStore leg
always; the Postgres leg when the label names `postgres` and the
DSN is set, skipped loudly otherwise, naming which. Both mount a scope
and initialize `/mcp` over the socket. Since 1.11.0 the
image runs the Rust server. It now declares `file,postgres`; both legs
run when the test DSN is set.
The test waits for both `/health` and Docker's built-in health status,
so it also exercises the binary's credential-free probe.

**Off means the tier self-skips whole** — `make test` and keyless boot
never need a docker daemon.

## The differential tier: a binary, not a key

Setup and configuration comparisons use temporary homes and client
directories, synthetic keys, and client CLI stand-ins. They never invoke
the machine's installed clients. Status compares all findings while
checking each implementation's own installation shape and upgrade command.

`make test-differential` replays the corpus through the Python shell and
the Rust binary over one seeded home (DESIGN §39.4); `make bench-hooks`
times both.

- `NEOSIAN_BINARY`: the binary to hold against the shell. Unset means
  `target/release/neosian`, then `target/debug/neosian`; neither built
  means every test in the tier self-skips. CI's `rust` job builds the
  release binary on its Linux x86_64 leg and runs both.

**Off means the tier self-skips whole.** `make test` never collects it
(the `differential` marker is excluded like the external tiers). CI's `container` job builds the image and
runs the image's declared backends on every push, no secret; the registry push is
`publish-python.yml`'s, on a release tag (DESIGN §29 — ledger #114's
built-and-smoked posture ended at NX).

## The local lane: a URL, not an API key

The `external_local` suite measures a local model on the shipped pack
(NW2, DESIGN §31.6): the door signs nothing, so its one key is where the
server listens.

- `NEOSIAN_TEST_LOCAL_URL`: the OpenAI-compatible base URL of a running
  llama-server (`http://127.0.0.1:8080/v1`) serving the lane's model,
  `ggml-org/gemma-4-E4B-it-GGUF:Q4_0` with `--jinja -c 32768`.
  `scripts/local_server.sh` starts one in docker and waits for it; with
  llama.cpp installed, `llama-server -hf ggml-org/gemma-4-E4B-it-GGUF:Q4_0
  --jinja -c 32768` is the same server (the model's default thinking on,
  the measured shape). Unset means every test in the lane self-skips
  per test (falsiness).
- `NEOSIAN_TEST_LOCAL_MODEL`: the server's name for the weights when
  it is not `gemma-4-e4b-it`: `gemma4:e4b` probes the Ollama recipe
  through the same lane. Optional.

**Off means the lane self-skips whole.** CI's `local` matrix entry sets
the URL after the script, with no secret; a CPU runner is slow, so the
cells its budget cuts are recorded `timeout` reds, never a hang. The
measured board in `baselines.md` was read from a machine with a GPU.

## CI secrets

One secret per key, named exactly like the env key — the three adapters'
keys plus the door lanes' four; the local lane has none. The dispatch-only `external` job
runs a `provider` matrix, one lane per suite, and injects only that lane's
secrets as env (no schedule — real-API runs are deliberate acts, ledger
#89); until a secret exists its lane passes vacuously via the empty-string
self-skip. The lint and unit-test jobs must never receive a secret.

## Publishing

A stable `v*` tag runs cargo-dist's generated `release.yml`: the five
binary targets, installers, checksums and GitHub attestations are hosted
on the GitHub Release, then `publish-python.yml` and
`publish-channels.yml` run. Announcement waits for both. A pre-release
hosts binary artifacts without publishing the package-manager channels.
Publication is a release operation; the default gates remain keyless.

Python publication uses PyPI trusted publishing for `mausa-ai/neosian`,
workflow `publish-python.yml`, environment `pypi`, and the job's OIDC
identity. GHCR uses the workflow's `GITHUB_TOKEN`. A dispatch of that
workflow rehearses the build without either upload.

The binary channels require this setup before the R3 release tag:

| Channel | Publisher setup | Without it |
|---|---|---|
| npm `neosian` | Create the GitHub environment `npm` on `mausa-ai/neosian`. In the existing npm package's publishing settings, configure GitHub trusted publishing: owner `mausa-ai`, repository `neosian`, calling workflow `release.yml`, environment `npm`. Node 24 supplies the required npm CLI; no npm token is used. | The npm publication fails; the release is not announced complete. |
| crates.io `neosian` | Create the GitHub environment `crates-io` on `mausa-ai/neosian`. First publication needs a crates.io account with verified email and a token permitting creation of `neosian`, stored there as environment secret `CARGO_REGISTRY_TOKEN`. After that first publish, configure trusted publishing for `mausa-ai/neosian`, calling workflow `release.yml`, environment `crates-io`, and delete the bootstrap secret. | The first publication cannot create the crate; subsequent publication needs either the bootstrap token or configured OIDC trust. |

npm and crates.io retries compare the published archive's digest and skip
only identical content; a conflicting version is an error. Homebrew
distribution is deferred; no tap or Homebrew publication job is required.
A dispatch of `publish-channels.yml` verifies the source package; none of
its publication steps run. The workflows never print credentials.
The crate jobs require a clean source tree before staging the asset
snapshot. Cargo's `--allow-dirty` admits those generated, ignored files.

Both npm and crates.io validate the calling `release.yml` workflow when
using these reusable workflows. Configure trust shortly
before publication: npm currently requires the first publish within two
days of creating its trust configuration. See the
[npm trusted-publisher documentation](https://docs.npmjs.com/trusted-publishers/)
and [crates.io trusted publishing](https://crates.io/docs/trusted-publishing).

For the release, verify the same version through all five binary archive
targets, the Windows MSI, and fresh npm and Cargo installations.
After publication, dispatch the `Verify release` workflow from `master`
with the stable version (without `v`). The verifier comes from the dispatch
ref; expected docs, version and toolchain come from a separate checkout of
the immutable release tag. This allows verifier repairs without replacing
published artifacts. It checks each archive's SHA-256, runs the binary
on its native platform, installs and uninstalls the MSI on Windows, and
checks fresh npm, Cargo and PyPI installs together on Linux. Every install
must report the release version and its embedded quickstart; both CLI
implementations must report their competing PATH entries.
The curl and npm rehearsals in CI use locally built native artifacts;
the live checks follow publication. Check `neosian version` and
`neosian docs quickstart` in each installed package, then `neosian status`
with the binary and Python shell both on PATH. Neither installation
requires migrating the existing home. Update the site's `RELEASE` only
after the package and release assets are available.

Platform publisher signing and Apple notarization are deferred. No signing
account or credential is needed for these builds. Browser downloads can
trigger platform security warnings; checksums and GitHub attestations are
available on the release.

## The curl installer

`scripts/install.sh`, served verbatim at `/install`, defaults to the binary.
`--python` keeps the uv tool route; `--find-links DIR` implies Python.
`NEOSIAN_VERSION` and `NEOSIAN_INSTALL_FIND_LINKS` mirror those flags.
`NEOSIAN_INSTALL_DIR` selects the binary directory, default
`~/.neosian/bin`; it does not move the state home. The installer links
`~/.local/bin/neosian` only if the name is free, and reports a competing
command without replacing it. The generated cargo-dist installer writes
the update receipt; shell profile files are left alone. SHA-256 tooling is
required: `sha256sum`, or a private adapter for macOS's `shasum`.
`UV_INSTALL_DIR` controls where the Python route finds a newly installed uv.
An explicit `--python` installation may replace this installer's own PATH
link with uv's shim; the binary and its update receipt stay in place.
