---
title: Quickstart — the stateless core and the opt-in layers
summary: Install, boot keylessly, run an agent, wrap it in a Conversation
---

# Quickstart

neosian is an async-only Python library (>= 3.12) for LLM agents:
tools, orchestration, streaming, fallback, guardrails, structured
output — over a stateless core, with durable conversations and
agent-curated memory as opt-in layers.

## Install

The repository is private; install from the git URL, pinned to an
annotated release tag (never master — extras ride the same URL):

```bash
uv add "neosian @ git+ssh://git@github.com/neosae/neosian@v<X.Y.Z>"
uv add "neosian[postgres] @ git+ssh://git@github.com/neosae/neosian@v<X.Y.Z>"
uv add "neosian[mcp] @ git+ssh://git@github.com/neosae/neosian@v<X.Y.Z>"
uv add "neosian[otel] @ git+ssh://git@github.com/neosae/neosian@v<X.Y.Z>"
uv add "neosian[server] @ git+ssh://git@github.com/neosae/neosian@v<X.Y.Z>"
```

Substitute the current release tag (the `llms.txt` beside this wheel
names it). The core install is database-driver-free, MCP-free, and
server-free.

## Keyless boot

A provider is available when its API key env var is set
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CEREBRAS_API_KEY`). `Model.FAKE`
is always available — deterministic, scripted, zero accounts — so you
can verify an install with nothing set:

```python
import asyncio
from neosian import Agent, AgentConfig, Message, Model, Role

config = AgentConfig(system_prompt="Be concise.", model=Model.FAKE)

async def main() -> None:
    response = await Agent(config=config).run(
        [Message(role=Role.USER, content="hello")], stream=False
    )
    print(response.message.content)

asyncio.run(main())
```

## The two rules

- **Async-only.** Every entry point is `async`; the library never calls
  `asyncio.run` for you (it deadlocks in notebooks and servers). The
  shell commands (`neosian …`) are the CLI tier and own their own loop.
- **Stateless core.** `Agent` stores nothing between calls. History and
  memory are opt-in layers you construct deliberately — never state
  smuggled into the agent.

## The durable thread

A store plus a conversation id turns the stateless agent into a
persistent, memory-bearing thread:

```python
from neosian import Conversation, FileStore, home, project_scope

store = FileStore(home())                # ~/.neosian; or PostgresStore(dsn)
convo = Conversation(config, store=store,
                     conversation_id="thread-829",
                     memory_scope=project_scope())   # user:<login>/proj:<dir>
async with convo:
    response = await convo.send("Where did we leave off?")
```

`home()` is the one place every neosian door shares — `~/.neosian`, or
`$NEOSIAN_HOME` — and `project_scope()` spells this directory's scope,
the same one a Claude Code session's hooks write to from here (`neosian
docs agents`); any scope string works in its place.

Resume is constructing again with the same id. History is append-only;
log-projection compaction (default-on) pages aged turns out of context
and a built-in `recall_turn` tool re-hydrates any of them verbatim —
what is stored never changes.

## Bring an OpenAI-compatible model

Models the `Model` enum lacks — a fine-tune, a local server, a provider
not shipped — register once at import through a door: the endpoint, the
env var that signs requests, and the dialect quirks the wire has. The
doors neosian ships are already registered: `from neosian.catalog import
GROK_4_6, GEMINI_3_7_FLASH` (xAI `XAI_API_KEY`, Gemini `GEMINI_API_KEY`).

```python
from neosian import AgentConfig, ModelPricing, OpenAICompatible, register_model

acme = OpenAICompatible(name="acme", api_key_env="ACME_API_KEY",
                        base_url="https://llm.acme.example/v1", temperature=True)
ACME_LARGE = register_model("acme-large", provider=acme, context_window=131_072,
                            max_output_tokens=16_384,
                            pricing=ModelPricing(input_per_mtok=3_000_000,
                                                 output_per_mtok=15_000_000))
config = AgentConfig(system_prompt="Be concise.", model=ACME_LARGE)
```

Cost in µ$, the context policy, capability-aware fallback and the
playground picker treat it like a shipped model; a missing `XAI_API_KEY`
fails naming it.

## Where to go next

- `neosian docs memory` — how the memory layer thinks.
- `neosian docs cli` — operate memory from the shell, no Python needed.
- `neosian docs topology` — who runs neosian code, where the bytes live.
