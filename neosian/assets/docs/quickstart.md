---
title: "Quickstart: the stateless core and the opt-in layers"
summary: Install, boot keylessly, run an agent, wrap it in a Conversation
---

# Quickstart

neosian is an async-only Python library (>= 3.12) for LLM agents:
tools, orchestration, streaming, fallback, guardrails, structured
output — over a stateless core, with durable conversations and
agent-curated memory as opt-in layers.

## Install

From PyPI, pinned to a release — the `llms.txt` beside this wheel names
the version:

```bash
uv add "neosian==<X.Y.Z>"
```

One package, everything but a database driver: the library with its
provider SDKs, the `neosian` shell, the MCP server and client, the state
process and OpenTelemetry spans — about 70 MB, none of it loaded
until used. Keyless to start: `Model.FAKE` needs no account and
`FileStore` is a directory. The one extra is the Postgres driver for
`PostgresStore`, `uv add "neosian[postgres]==<X.Y.Z>"`, with `[all]` as
its alias; the former `[cli]`, `[mcp]`, `[otel]` and `[server]` resolve
for one release and add nothing. On a machine with nothing on it,
`curl -fsS https://neosian.com/install | bash` lands uv and neosian,
saying what it installs first.

On your own machine, three commands and no Python: `neosian status`
says whether it is set up, `neosian setup --write` wires every installed
agent (Claude Code, Codex, OpenCode) to the home, and bare `neosian`
opens a chat with an agent that knows neosian and writes to the same
memory (`neosian docs cli`).

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
what is stored never changes. A long URL, path or id in an aged turn is
never cut: it renders as a `[link N]` handle the model passes as written
in any tool call, expanded before the tool runs.

## Bring an OpenAI-compatible model

Models the `Model` enum lacks — a fine-tune, a local server, a provider
not shipped — register once at import through a door: the endpoint, the
env var that signs requests, and the dialect quirks the wire has. The
door rows neosian ships are `Model` members like every other row —
`Model.GROK_4_6` (xAI, `XAI_API_KEY`), `Model.GEMINI_3_8_FLASH` (Gemini,
`GEMINI_API_KEY`) — and every shipped or registered row answers to its
wire id: `AgentConfig(model="gpt-5.6-sol")` builds the same agent as the
member.

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
fails naming it. The door's dialect knobs default to OpenAI's wire:
`temperature`, `reasoning_effort`, `reasoning_field`, `strict_schemas`,
`json_mode="json_object"` (structured output as the plain JSON mode, the
schema in the system prompt — DeepSeek) and `echo_reasoning` (the
reasoning field sent back on assistant turns — a 400 in tool loops
without it on DeepSeek, Qwen and Kimi).

## Where to go next

- `neosian docs memory` — how the memory layer thinks.
- `neosian docs cli` — operate memory from the shell, no Python needed.
- `neosian docs topology` — who runs neosian code, where the bytes live.
