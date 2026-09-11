<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mausa-ai/neosian/v1.0.0rc4/branding/logo-dark.svg">
  <img src="https://raw.githubusercontent.com/mausa-ai/neosian/v1.0.0rc4/branding/logo-light.svg" alt="neosian" width="280">
</picture>

# neosian

[![PyPI](https://img.shields.io/pypi/v/neosian.svg?include_prereleases&color=6d6d32)](https://pypi.org/project/neosian/)
[![Docs](https://img.shields.io/badge/docs-docs.neosian.com-6d6d32.svg)](https://docs.neosian.com)
[![CI](https://github.com/mausa-ai/neosian/actions/workflows/ci.yml/badge.svg)](https://github.com/mausa-ai/neosian/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-6d6d32.svg)](https://github.com/mausa-ai/neosian/blob/v1.0.0rc4/LICENSE)
[![Python 3.12 | 3.13 | 3.14](https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14-6d6d32.svg)](https://github.com/mausa-ai/neosian/blob/v1.0.0rc4/pyproject.toml)

**The state layer for LLM agents**: durable conversations, agent-curated
memory, skills, a shared board and a ledger of who did what — on storage
you own (a directory, your Postgres, or one small state process), for
the agent you already use or the one you build.

Two doors, one store:

- **Build an agent.** An async-only, stateless `Agent` (tools,
  streaming, fallback, guardrails, structured output) that a
  `Conversation` wraps into a durable, memory-bearing thread.
- **Give your agent state.** Claude Code, Codex, Cursor or OpenCode get
  memory and skills over MCP and every session recorded through hooks —
  `neosian setup --write` wires every client it finds; `neosian status`
  says whether the machine is set up; bare `neosian` opens a chat with
  an agent that knows neosian, on the same memory.

<p align="center">
<img src="https://raw.githubusercontent.com/mausa-ai/neosian/v1.0.0rc4/branding/readme/record.gif" alt="neosian record install writes the hooks; one headless Claude Code session lands as a recorded turn; neosian audit names it" width="720">
<br>
<img src="https://raw.githubusercontent.com/mausa-ai/neosian/v1.0.0rc4/branding/readme/left-off.gif" alt="The next Claude Code session opens on where we left off and answers from the record" width="720">
</p>

*A Claude Code session landing as a recorded turn that `neosian audit`
names, and the next session opening on where we left off — no key of
yours, no flags: the home and this project's scope.*

Agents read this repository too: [llms.txt](https://github.com/mausa-ai/neosian/blob/v1.0.0rc4/llms.txt) is the machine
front door, `neosian docs` prints the shipped docs pages from the wheel
(version-true), and every shell verb has `--json` and exit tiers — one
CLI for humans and agents (`neosian docs cli`).

```python
store = FileStore(home())                          # ~/.neosian, or your PostgresStore(dsn)
convo = Conversation(config, store=store, conversation_id="thread-829",
                     memory_scope=project_scope())  # user:<login>/proj:<dir>
response = await convo.send("Where did we leave off?")
```

## Install

Requires Python >= 3.12. One package, everything in it — on PyPI as a
pre-release until v1.0.0, so pin it:

```bash
uv add "neosian==1.0.0rc4"
```

The install brings the library with its three provider SDKs (OpenAI,
Anthropic, Cerebras — xAI and Gemini ride the OpenAI wire), the `neosian`
shell, the MCP server and client, the state process and OpenTelemetry
spans: about 70 MB on disk, none of it loaded until used. Nothing in
it needs an account or a server to start — `Model.FAKE` runs keylessly
and `FileStore` is a directory. The one extra is the Postgres driver,
`uv add "neosian[postgres]==1.0.0rc4"`, for a `PostgresStore` against a
server you already run; `[all]` is its alias, and the former `[cli]`,
`[mcp]`, `[otel]` and `[server]` still resolve for one release and add
nothing.

For a machine with nothing on it, the one-liner is
[scripts/install.sh](https://github.com/mausa-ai/neosian/blob/v1.0.0rc4/scripts/install.sh): it says what it
installs, finds uv or installs it from a pinned release, runs `uv tool
install neosian`, checks the PATH and prints the registration command.
It is the same two commands, spelled out beside it:

```bash
curl -fsS https://neosian.com/install | bash
# or, by hand:
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install "neosian==1.0.0rc4"
```

To work on neosian itself, `uv sync --all-groups` then `uv run neosian
version` (the Development section has the gates).

## Quickstart

Keyless boot is an invariant: `Model.FAKE` is always available, so an
install verifies with no account. The fake is scripted through the
`client_factory` seam — the same seam the unit tier and the eval
baseline use:

```python
import asyncio

from neosian import Agent, AgentConfig, Message, Model, Role
from neosian.fake import FakeClient, FakeScript, FakeTurn

fake = FakeClient(FakeScript(turns=(FakeTurn(content="hello from the fake"),)))
config = AgentConfig(system_prompt="Be concise.", model=Model.FAKE,
                     client_factory=lambda _: fake)


async def main() -> None:
    response = await Agent(config=config).run(
        [Message(role=Role.USER, content="hello")], stream=False
    )
    print(response.message.content)


asyncio.run(main())
```

Set a key and name a real model (`model=Model.CLAUDE_SONNET_5`, the
providers table below); tools are decorated functions whose signature is
the schema (`neosian docs tools`). [examples/](https://github.com/mausa-ai/neosian/tree/v1.0.0rc4/examples) has one runnable
file per feature, every one importing keylessly.

## What is in the box

Each line is one page in the wheel — `neosian docs <topic>`.

- **Conversation and memory** (`memory`). History is an append-only log;
  log-projection compaction pages aged turns out of context and
  `recall_turn` re-hydrates any of them verbatim — paging, not deletion.
  Memory is file-school: small markdown documents with frontmatter under
  mounted scopes, read and written through one `memory` tool (`view`,
  `create`, `str_replace`, `insert`, `delete`, `rename`); reflection at
  the close distills the session into deliberate writes. Every write is
  a full-content version row with an actor; a `memory_write` frame on
  the stream carries `{command, path, version}` so a host renders
  "remembered X" with a working `revert_memory` undo.
- **The shell and the ledger** (`cli`). The six memory commands, the
  gardener (`maintain`), the operator verbs (`versions`, `redact`,
  `revert`), `audit` — what was done, by whom, when, on any substrate —
  and `export`/`import`, which move a store whole, history included.
- **Skills** (`skills`). A skill is a document under `skills/<name>` in
  a mount: versioned, curated by the mount flag, loaded by
  `list_skills`/`load_skill`, served over MCP as a prompt (a slash
  command in Claude Code).
- **MCP, both directions** (`mcp`). `python -m neosian.mcp` serves the
  store to any client over stdio; `McpServer.stdio/http/in_process`
  consumes any MCP server as agent tools — schema verbatim, `is_error`
  in-band, the approval gate and hooks unchanged.
- **The agent you already use** (`agents`). `neosian record install`
  spells the hooks that land a prompt-to-stop span as one turn by
  `claude-code:<session>` (Codex and OpenCode too), plus a sessions
  document; `SessionStart` prints the index and "where we left off" into
  the next window. A neosian `Conversation` on the same home shares the
  scope, so one `audit` names both.
- **The state process** (`topology`). `neosian serve` puts memory and
  conversations on a port (the shipped Dockerfile is the appliance) — reach, not capability; `RemoteStore` drops in where
  `FileStore` does, core install. Bearer tokens env-only, per-client
  when you want the ledger to know who wrote.
- **The agent core** (`agent`, `tools`). `run(stream=True)` yields ten
  frozen `AgentEvent` dataclasses a host relays over SSE; `AgentHooks`
  observe every turn, call and tool; `ToolGateConfig` routes every tool
  call through one approver, and no decision is a denial; capability-
  aware fallback, guardrails on any model, structured output, prompt
  caching, multimodal blocks on Anthropic.

## Providers

API keys are read from environment variables; a provider is available when
its key is set (asking for an unavailable model raises
`MissingAPIKeyError`).

| Provider | Env var | Models |
|---|---|---|
| A registered door | the door's `api_key_env` | The day-one door for any model neosian has not shipped: any OpenAI-compatible endpoint via `register_model` (`neosian docs quickstart`) |
| OpenAI | `OPENAI_API_KEY` | `gpt-5.6-sol` (default), `gpt-5.6-terra`, `gpt-5.6-luna` (reasoning, `max` effort), `gpt-5.1` |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-fable-5-1`, `claude-opus-5`, `claude-sonnet-5` (default), `claude-haiku-4-5` (retirement floor 2026-10-15); vision/PDF input, prompt caching, adaptive thinking |
| Cerebras | `CEREBRAS_API_KEY` | Default provider: `gpt-oss-120b`, `qwen-3.8-27b` |
| xAI | `XAI_API_KEY` | `grok-4.6` — a shipped door row (`Model.GROK_4_6`) |
| Google Gemini API | `GEMINI_API_KEY` | `gemini-3.8-flash`, `gemini-3.7-flash` on the OpenAI-compatible endpoint (`Model.GEMINI_3_8_FLASH`; the introductory card through 2026-12-31) |
| Fake | — | Keyless, deterministic, always available (`Model.FAKE`) |

Every shipped row is a `Model` member and answers to its wire id too
(`AgentConfig(model="gpt-5.6-sol")`); each carries its provider's
lifecycle as data (`Model.X.spec.retires`, `card_until`) and is earned by
green dispatched runs of the memory baselines — membership is measured,
never assumed (`neosian docs baselines`).

## Storage

`MemoryStore` and `ConversationStore` are the contracts: async ABCs that
own no connection, commit no transaction, issue no DDL. `FileStore` (a
plain directory), `PostgresStore` (the `postgres` extra; schema by
`python -m neosian.schemas postgres | psql "$DSN"`) and `RemoteStore`
(the state process's wire) implement both; a host may implement its own,
kept honest by the shipped `MemoryStoreContract` /
`ConversationStoreContract` conformance kits. One writer per FileStore
root; many workers on one Postgres.

## Evaluation

`neosian eval suite.yaml` runs YAML suites and exits nonzero on failure —
an agent suite over variants × models × cases, and `kind: memory`, which
scores store truth across scripted sessions on every transport. The
shipped memory pack is all-green on `models: [fake]`, and the same pack
against the real providers produces the
[baselines page](https://github.com/mausa-ai/neosian/blob/v1.0.0rc4/neosian/assets/docs/baselines.md),
fingerprint-gated so a prompt change without a recorded re-run fails
`make test`.

## Stability

`v1.0.0` is deliberately not yet cut. When it is, it will carry the API
stability promise: `Agent`, `Conversation`, `MemoryStore`, and the
`neosian.evaluation` facade stable under SemVer, the ecosystem
seams (scope grammar, token classes, integer
micro-USD, event vocabulary, error codes) SemVer-guaranteed — a seam break
only at a major — and the state process's wire (the twelve `/v1/` store
routes and their envelope, versioned by `WIRE_VERSION`) stable under the
same promise: one promise covering library, seams, and wire. Until then the seams are append-only by convention, and error
codes are already append-only forever. Consumers pin a release
(`neosian==X.Y.Z`), never master. Model ids follow their providers'
lifecycles: a retired id leaves in the next release after its provider's
date, named in the changelog; each shipped row's clock is data on its
spec (`neosian docs baselines`).

## Development

```bash
make install    # uv sync --locked --all-groups
make lint       # ruff + black --check + import-linter
make typecheck  # mypy --strict neosian tests examples
make test       # unit tier — the default gate, zero API keys
make size       # file-size gate (warn 300 / fail 500)
make test-external provider=anthropic file=~/path/to/creds  # real API calls
make test-postgres                                      # needs NEOSIAN_TEST_POSTGRES_DSN
make test-container                                     # both kits vs the container (docker)
```

The default gate needs no accounts; external tiers inject credentials
value-blind. Sign off every commit (`git commit -s`): contributions are
accepted under the [Developer Certificate of
Origin](https://developercertificate.org/) and licensed as the project
is, Apache-2.0 — there is no CLA. Open an issue before a feature or a
departure from documented behaviour; the public API is pinned by
`tests/unit/test_init.py`, so an export change is a reviewed diff.
[SECURITY.md](https://github.com/mausa-ai/neosian/blob/v1.0.0rc4/SECURITY.md) is where vulnerabilities go, never an issue.

## Documents

- `neosian docs <topic>` — the shipped pages, from the wheel: `quickstart`,
  `agent`, `tools`, `memory`, `skills`, `cli`, `mcp`, `agents`, `topology`,
  `baselines` (the published per-provider memory numbers); the same pages
  online at [docs.neosian.com](https://docs.neosian.com), rendered from the
  wheel at the current release.
- [SERVICES.md](https://github.com/mausa-ai/neosian/blob/v1.0.0rc4/SERVICES.md) — every env key and what turning it off means.
- [CHANGELOG.md](https://github.com/mausa-ai/neosian/blob/v1.0.0rc4/CHANGELOG.md) — Keep a Changelog, one section per release.
- [SECURITY.md](https://github.com/mausa-ai/neosian/blob/v1.0.0rc4/SECURITY.md) — where to report, and the supported line.
- [llms.txt](https://github.com/mausa-ai/neosian/blob/v1.0.0rc4/llms.txt) — the machine-readable front door (byte-identical
  twin ships in the wheel).

---

Be kind and constructive in every project space; the maintainers read
**community@neosian.com**.
