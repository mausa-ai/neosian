# Services & keys

> Companion to [CLAUDE.md](../CLAUDE.md) (conventions), [DESIGN.md](DESIGN.md)
> §10–§11 (harness and CI), and [ECOSYSTEM.md](ECOSYSTEM.md) (frozen seams).

**Keyless boot is the invariant**: `make lint`, `make typecheck` and
`make test` need **zero third-party accounts**. FakeProvider
(`Provider.FAKE`, `Model.FAKE*` — `neosian.fake`) is the keyless path; the
CI test matrix carries no secrets at all, and that absence is the assertion.

Keys are read from the process environment at client construction (never at
import); `~/.neosian/config.toml` (written by `neosian configure`) fills in
for interactive CLI use, with env vars taking precedence. A provider is
available exactly when its key is set; asking for an unavailable provider
raises `MissingAPIKeyError` (`agent_missing_api_key`).

## Optional — each key unlocks a real provider

| Key | Provider | Off means |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic (Claude; vision/PDF, prompt caching, adaptive thinking) | Claude models leave the router; `external_anthropic` self-skips; playground hides the Anthropic menu |
| `OPENAI_API_KEY` | OpenAI (the GPT-5.6 family and `gpt-5.1`, reasoning) | GPT models leave the router; `external_openai` self-skips; playground hides the OpenAI menu |
| `CEREBRAS_API_KEY` | Cerebras (default provider; `gpt-oss-120b`, `qwen-3.8-27b` — shipped door rows on the OpenAI wire since #218) | The rows stay listed but their first call raises `MissingAPIKeyError` naming the var; `external_cerebras` self-skips |
| `XAI_API_KEY` | xAI (`grok-4.6`, a shipped door row — `Model.GROK_4_6`, DESIGN §31) | The row stays listed but its first call raises `MissingAPIKeyError` naming the var; `external_xai` self-skips |
| `GEMINI_API_KEY` | Google Gemini API, OpenAI-compatible endpoint (`gemini-3.8-flash` and `gemini-3.7-flash`, shipped door rows) | As above; `external_gemini` self-skips |

With **no** keys set, the library still imports, constructs, runs (on
FakeProvider), and passes its full default test tier.

A registered door (`register_model`, DESIGN §19) reads its own key from the
env var the door names — `OpenAICompatible(api_key_env=...)` — env only,
never argv; a missing one fails at the first call naming it, and guardrails
on a door need it at construction. `neosian configure` does not manage door
keys.

### Candidate doors — the external suites only, until a row is green

Nothing in the library reads these. Each drives one lane of NW's gate
(`tests/external/lanes.py`, DESIGN §19.7); off means that suite
self-skips. A green row is promoted into the table above (the way
`XAI_API_KEY` and `GEMINI_API_KEY` were, 2026-09-02); a red one exits
whole (DeepSeek and Alibaba Model Studio did, the same day — BASELINES.md
keeps their runs; both re-entered under NW1, ledger #211 — the lanes
below, their first boards owed to NW1's dispatch).

| Key | Suite | Serving stack |
|---|---|---|
| `MOONSHOT_API_KEY` | `kimi` | Moonshot (`kimi-k3`) |
| `DEEPSEEK_API_KEY` | `deepseek` | DeepSeek (`deepseek-flash`; `json_mode="json_object"` + the reasoning echo) |
| `DASHSCOPE_API_KEY` | `qwen` | Alibaba Model Studio, Singapore, the token plan (`qwen3.8-max`; the reasoning echo) |

Where an account tier caps requests per minute, the lane — shipped or
candidate — is paced on our side (`requests_per_minute` in
`tests/external/lanes.py`, `tests/external/pacing.py`): the lane takes
longer, never runs fewer cells.

## The external suites

`make test-external provider=<openai|anthropic|cerebras|xai|gemini|kimi|deepseek|qwen>`
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
make test-postgres
```

CI supplies the DSN from a service container — never a secret — so the
`postgres` job runs on every push and PR, unlike the dispatch-only
provider jobs (DESIGN §12 ledger #40, #89). Each test creates and drops its
own uniquely-named schema; the server keeps no state between runs.

`NEOSIAN_EXAMPLE_POSTGRES_DSN` is read only by
`examples/fastapi_chatbot.py` — the library itself never reads a DSN
from the environment; the MCP entry point does (below). Unset, the
example app still boots (construction is pure validation) and logs a
warning; requests fail at first pool use.

## The home — `NEOSIAN_HOME`

`NEOSIAN_HOME` moves the home (DESIGN §22): the FileStore every argv
entry point — `neosian memory`, `audit`, `mcp`, `record`, both
installers, `serve` — and the playground use when `--root`, `--url` and
the DSN are all absent, and where `neosian record` spools. Unset, the
home is `~/.neosian`. An explicit flag always wins; the unit tier points
it at a temporary directory for every test, so no keyless run touches
the real one.

## The update knob — in the config, not the environment

`[update] mode = off | notify | auto` lives in `<home>/config.toml`
(`neosian update --mode M` sets it; default `off`), deliberately not
an environment variable: the check contacts PyPI's simple index and
the opt-in is the asking (DESIGN §30.3, ledger #214). It runs only on
the human door — bare `neosian`, `chat`, `status`, `playground`,
`configure`, on a terminal, never under `--json` — never on `memory`,
`audit`, `export`, `import`, `record`, `mcp` or `serve`; once per 24 h
by a stamp under the home; silent when offline. `NEOSIAN_INSTALL`
(set to `container` by the image) names the installation shape
`status` and `update` report — `auto` applies only the uv tool shape.

## The scope — `NEOSIAN_SCOPE`

`NEOSIAN_SCOPE` is `--scope`'s environment twin (DESIGN §30): read by
the same argv entry points, it names the single read-write mount at
`/memories` when no flag does (`audit` reads it as the raw scope). Unset,
the shell verbs resolve the working directory's project layout — the
`user:<login>` + `user:<login>/proj:<slug>` pair the installers render —
and a directory with no derived name refuses at exit 2. An explicit
`--scope` or `--mount` always wins.

## The MCP server — a DSN, not an API key

`NEOSIAN_POSTGRES_DSN` (renamed from `NEOSIAN_MCP_POSTGRES_DSN` at NA,
ledger #76) is read only by the argv entry points — `python -m
neosian.mcp` / the `neosian mcp` pass-through, `neosian memory` /
`python -m neosian.memory`, and `neosian serve` / `python -m
neosian.server` — never by the library: all are processes configured
through argv, and a DSN must not appear there — argv is world-readable
in `ps` (DESIGN §12 ledger #53). Unset, the entry points require
`--root PATH` and use a FileStore; set, they use a `PostgresStore` and
`--root` is refused (an explicit conflict, no precedence rule). None
ever applies the DDL — run `python -m neosian.schemas postgres | psql`
first (DESIGN §8 C1).

## The state process — bearer tokens, not an API key

`NEOSIAN_SERVE_TOKEN` is read only by `neosian serve` / `python -m
neosian.server`. There is deliberately no `--token` flag: the argv rule
above applies to a token exactly as it does to a DSN (ledger #53). Since
NL (DESIGN §20) the value is a **table** — `actor=token[,actor=token…]`,
each actor in the grammar (`claude-code:laptop=…,app:kit=…`) — or one
bare token, which is the single client `client:default`. The process
records every write under the presenting token's actor
(`<client>[/<what the client said>]`); a malformed table refuses to
start at the grammar tier naming the entry, never the secret.

`NEOSIAN_CLIENT_TOKEN` is the **client's** side: read only by the argv
entry points when `--url` names a state process (`neosian memory`,
`neosian mcp`, `neosian audit`), never by the library
(`RemoteStore.connect(url, token=…)` takes it explicitly). A distinct
key because one machine runs both; `--url` without it refuses at the
grammar tier. Off means no daemon store from the shell.

**Off means the process refuses to start** — exit 2 at the grammar tier,
naming the key. Default-deny is code, not configuration (ledger #107,
applied to the wire at #110): the state process never serves
unauthenticated, because TLS terminates at a reverse proxy and an open
port would otherwise expose every scope in the store. Clients send
`Authorization: Bearer <token>`; `RemoteStore.connect(url, token=…)`
does it for them and names this key when the server rejects the token.
`/health` is the one unauthenticated route (a container healthcheck
carries no credentials) and reports liveness only.

## The container suite — a URL, not an API key

The `external_server` tier points `RemoteStore` at a **running
state-process container** (NM's done-when; §18.9). Four keys, all set
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

**Off means the tier self-skips whole** — `make test` and keyless boot
never need a docker daemon. CI's `container` job builds the image and
runs both legs on every push, no secret; the registry push is
`release.yml`'s, on a release tag (DESIGN §29 — ledger #114's
built-and-smoked posture ended at NX).

## CI secrets

One secret per key, named exactly like the env key — the three adapters'
keys plus the door lanes' three. The dispatch-only `external` job
runs a `provider` matrix, one lane per suite, and injects only that lane's
secrets as env (no schedule — real-API runs are deliberate acts, ledger
#89); until a secret exists its lane passes vacuously via the empty-string
self-skip. The lint and unit-test jobs must never receive a secret.

## Publishing

No key. `release.yml` publishes to PyPI by trusted publishing — the
job's OIDC token (`id-token: write` on the `pypi` job alone) is
exchanged by uv for a one-use upload token that PyPI accepts only from
this repository's workflow — and pushes the image to GHCR with the
workflow's own `GITHUB_TOKEN` (`packages: write` on the image jobs).
A long-lived publishing credential never exists, so there is nothing
to rotate or leak; the lint and unit-test jobs still never receive a
secret, and a `workflow_dispatch` of the release workflow rehearses
every step but the two pushes with nothing at all (DESIGN §29).
