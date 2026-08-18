# Services & keys

**Keyless boot is the invariant**: `make lint`, `make typecheck` and
`make test` need **zero third-party accounts**. FakeProvider
(`Provider.FAKE`, `Model.FAKE*` — ships in phase NS) is the keyless path; the
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

- `file=<envfile>` routes through `scripts/external_env.py` (phase NH):
  **value-blind** injection — a per-suite allowlist names exactly which keys
  the suite may see; stdout prints key *names* only, never values.
- Suites skip **per test** via a `_client_or_skip()`-style helper — never a
  module-level skip (an empty selection makes pytest exit 5).
- The presence check tests **falsiness**, not `None`: an absent GitHub
  Actions secret arrives as the empty string.

## CI secrets

One secret per provider, named exactly like the env key. The scheduled /
dispatch `external-<provider>` jobs inject them as env; until a secret exists
its job passes vacuously via the empty-string self-skip. The lint and
unit-test jobs must never receive a secret.
