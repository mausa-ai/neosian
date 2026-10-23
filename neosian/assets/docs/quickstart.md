---
title: "Quickstart: state for the agent you use, then the one you build"
summary: Install, set up the agent you use, boot keylessly, wrap an agent in a Conversation
---

# Quickstart

neosian is the state layer for LLM agents: durable conversations,
agent-curated memory, skills, a shared board and a ledger of who did
what, on storage you own (a directory, your Postgres, or one small state
process). It serves the agent you already use (Claude Code, Codex,
OpenCode, Muse Code, Cursor, Pi) over MCP and hooks, and the agent you build
on its async-only, stateless Python core (>= 3.12).

## Install

For an existing agent, the native binary needs no Python or uv:

```bash
curl -fsS https://neosian.com/install | sh
neosian setup --write
```

The installer verifies SHA-256, installs in `~/.neosian/bin`, and links
`~/.local/bin/neosian` when that name is free. It preserves an existing
Python command; use `~/.neosian/bin/neosian setup --write` to connect the
binary in that case. `status` names competing executables. The binary and Python
shell share the same home and client files, with no migration.
`--version X.Y.Z` selects a release. `--python` retains the uv tool path;
`--find-links DIR` selects a local Python wheel and implies `--python`.
Windows installers are on the GitHub Release. Publisher signing and Apple
notarization are deferred, so browser downloads can trigger security warnings.

The binary's package-manager commands are `npm install -g neosian` and
`cargo install neosian --locked`. Homebrew distribution is deferred.
npm uses prebuilt binaries; Cargo requires the crate's Rust version.
Update these installations through the package manager that installed them.

For chat, the TUI, playground and eval, install the Python shell with
`uv tool install "neosian==<X.Y.Z>"`, or run the curl installer with
`sh -s -- --python`. For the SDK, use `uv add "neosian==<X.Y.Z>"`.
The `llms.txt` beside the wheel names its version; Python requires >= 3.12.
The package includes the provider SDKs, MCP server and client, state process
and OpenTelemetry spans, about 70 MB, loaded on use. `Model.FAKE` and
`FileStore` need no account. The one extra is the Postgres driver:
`uv add "neosian[postgres]==<X.Y.Z>"`, with `[all]` as its alias.

## The agent you already use

Set up the agent you already use with the binary. `neosian status` says whether this machine
is set up. `neosian setup --write` asks which installed agents to connect (Claude
Code, Codex, OpenCode, Muse Code, Cursor, Pi) to the home once per machine:
memory and skills over MCP, every session recorded through the client's
own hooks, so a new project needs nothing. The Python shell's bare `neosian` opens a chat
with an agent that knows neosian, writes to the same memory and runs
these verbs for you, asking before one that changes anything
(`neosian docs cli`).

From then on each session lands as a turn the ledger names (the Python shell's `neosian
audit`), the next session opens on where you left off, "continue" in
any of them carries the last session on in one call (ask the departing
one to hand off and the next one starts with its note), and `neosian
search <words>` finds any turn by its words. A memory the agent writes is
a markdown file under the home you can read, version and revert
(`neosian docs agents`, `neosian docs memory`).

## Keyless boot

A provider is available when its API key env var is set
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `CEREBRAS_API_KEY`). `Model.FAKE`
is always available (deterministic, scripted, zero accounts), so you
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
  memory are opt-in layers you construct deliberately, never state
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

`home()` is the one place every neosian door shares (`~/.neosian`, or
`$NEOSIAN_HOME`) and `project_scope()` spells this directory's scope,
the same one a Claude Code session's hooks write to from here (`neosian
docs agents`); any scope string works in its place.

Resume is constructing again with the same id. History is append-only;
log-projection compaction (default-on) pages aged turns out of context
and two built-in tools reach every one of them: `search_history` finds
a turn by its words and `recall_turn` re-hydrates it verbatim: what is
stored never changes. A long URL, path or id in an aged turn is
never cut: it renders as a `[link N]` handle the model passes as written
in any tool call, expanded before the tool runs.

## Bring an OpenAI-compatible model

Models the `Model` enum lacks (a fine-tune, a local server, a provider
not shipped) register once at import through a door: the endpoint, the
env var that signs requests, and the dialect quirks the wire has. The
door rows neosian ships are `Model` members like every other row:
`Model.GROK_4_6` (xAI, `XAI_API_KEY`), `Model.GEMINI_3_8_FLASH` (Gemini,
`GEMINI_API_KEY`), `Model.KIMI_K3` (Moonshot, `MOONSHOT_API_KEY`), `Model.QWEN_3_8_MAX` (Alibaba Model Studio, `DASHSCOPE_API_KEY`). Every shipped or registered row answers to its
wire id: `AgentConfig(model="gpt-6.1-sol")` builds the same agent as the
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
playground picker treat it like a shipped model; a missing `ACME_API_KEY`
fails naming it. An endpoint that signs nothing (a local llama-server or
Ollama) is `api_key_env=None`: no variable is read, no key of yours is
sent, and the recipes are on `neosian docs local`. The door's `wire`
names the endpoint it speaks:
`"chat"` (Chat Completions, the default, what most compatible endpoints
serve) or `"responses"` (the Responses API, the wire OpenAI and xAI
recommend; run stateless, with the model's encrypted reasoning carried
on the message between tool calls and nothing stored at the provider).
The dialect knobs default to OpenAI's behaviour: `temperature`,
`reasoning_effort`, `strict_schemas`, and on `"chat"` also
`reasoning_field`, `json_mode="json_object"` (structured output as the
plain JSON mode, the schema in the system prompt: DeepSeek),
`echo_reasoning` (the reasoning field sent back on assistant turns: a
400 in tool loops without it on DeepSeek, Qwen and Kimi) and
`thinking_switch` (the endpoint's boolean thinking parameter, such as
Model Studio's `enable_thinking`: off unless a `reasoning_effort` is
asked).

Use `ModelSelector.CLAUDE_SONNET_LATEST` or `"openai:sol:latest"` to follow a
tier immediately, and its `DEFAULT` selector for the 30-day migration window.
Explicit model IDs are supported until the announced catalog removal. Effort
includes `XHIGH`; omitted effort preserves provider defaults. See
`neosian docs agent` for the complete policy and capability rules.

## Where to go next

- `neosian docs memory`: how the memory layer thinks.
- `neosian docs agents`: the record through hooks, client by client.
- `neosian docs cli`: operate memory from the shell, no Python needed.
- `neosian docs topology`: who runs neosian code, where the bytes live.
