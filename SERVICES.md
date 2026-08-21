# Services & keys

> Companion to [CLAUDE.md](CLAUDE.md) (conventions), [DESIGN.md](DESIGN.md)
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
| `OPENAI_API_KEY` | OpenAI (GPT-5 family, reasoning) | GPT models leave the router; `external_openai` self-skips; playground hides the OpenAI menu |
| `GROQ_API_KEY` | Groq (default provider; also powers guardrail policy checks) | Groq models leave the router; guardrails need another provider; `external_groq` self-skips |
| `CEREBRAS_API_KEY` | Cerebras | Cerebras models leave the router; `external_cerebras` self-skips |

With **no** keys set, the library still imports, constructs, runs (on
FakeProvider), and passes its full default test tier.

## The external suites

`make test-external provider=<groq|openai|anthropic|cerebras>` runs that
provider's real-API suite (`-m external_<provider>`).

- `file=<envfile>` routes through `scripts/external_env.py`:
  **value-blind** injection — a per-suite allowlist names exactly which keys
  the suite may see; stdout prints key *names* only, never values.
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
`postgres` job runs on every push and PR, unlike the schedule-only
provider jobs (DESIGN §12 ledger #40). Each test creates and drops its
own uniquely-named schema; the server keeps no state between runs.

`NEOSIAN_EXAMPLE_POSTGRES_DSN` is read only by
`examples/fastapi_chatbot.py` — the library itself never reads a DSN
from the environment; the MCP entry point does (below). Unset, the
example app still boots (construction is pure validation) and logs a
warning; requests fail at first pool use.

## The MCP server — a DSN, not an API key

`NEOSIAN_POSTGRES_DSN` (renamed from `NEOSIAN_MCP_POSTGRES_DSN` at NA,
ledger #76) is read only by the argv entry points — `python -m
neosian.mcp` / the `neosian mcp` pass-through, and `neosian memory` /
`python -m neosian.memory` — never by the library: both are processes
configured through argv, and a DSN must not appear there — argv is
world-readable in `ps` (DESIGN §12 ledger #53). Unset, the entry points
require `--root PATH` and use a FileStore; set, they use a
`PostgresStore` and `--root` is refused (an explicit conflict, no
precedence rule). Neither ever applies the DDL — run
`python -m neosian.schemas postgres | psql` first (DESIGN §8 C1).

## CI secrets

One secret per provider, named exactly like the env key. The scheduled /
dispatch `external-<provider>` jobs inject them as env; until a secret exists
its job passes vacuously via the empty-string self-skip. The lint and
unit-test jobs must never receive a secret.
