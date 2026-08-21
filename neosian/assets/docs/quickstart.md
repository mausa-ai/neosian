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
```

Substitute the current release tag (the `llms.txt` beside this wheel
names it). The core install is database-driver-free and MCP-free.

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
from neosian import Conversation, FileStore

store = FileStore(".neosian")            # or PostgresStore(dsn)
convo = Conversation(config, store=store,
                     conversation_id="thread-829",
                     memory_scope="user:1234")
async with convo:
    response = await convo.send("Where did we leave off?")
```

Resume is constructing again with the same id. History is append-only;
log-projection compaction (default-on) pages aged turns out of context
and a built-in `recall_turn` tool re-hydrates any of them verbatim —
what is stored never changes.

## Where to go next

- `neosian docs memory` — how the memory layer thinks.
- `neosian docs cli` — operate memory from the shell, no Python needed.
- `neosian docs topology` — who runs neosian code, where the bytes live.
