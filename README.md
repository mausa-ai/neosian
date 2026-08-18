# neosian

Stateless agentic AI library. LLM orchestration, tool execution, streaming.

Neosian is an async-only Python library for building agents on top of multiple LLM
providers behind one interface: define tools as decorated functions, configure an
agent, and run it — blocking or streaming — with parallel tool execution,
capability-aware model fallback, guardrails, structured output, and prompt caching
handled for you. The core is deliberately stateless: the caller owns conversation
history, and the agent stores nothing between calls.

## Install

Requires Python >= 3.12. The project is managed with [uv](https://docs.astral.sh/uv/):

```bash
uv sync --all-groups          # library + dev tools
uv run neosian version        # CLI sanity check
```

As a dependency of another uv project:

```bash
uv add "neosian @ git+ssh://git@github.com/<owner>/neosian"
```

## Quickstart

```python
import asyncio
from datetime import datetime

from neosian import Agent, AgentConfig, Message, Model, Role, Tool, ToolResult


@Tool(name="get_current_datetime", description="Get the current date and time")
async def get_current_datetime() -> ToolResult[str]:
    return ToolResult.ok(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


configuration = AgentConfig(
    system_prompt="You are a helpful assistant. Be concise.",
    tools=[get_current_datetime],
    model=Model.GROQ_GPT_OSS_20B,
)


async def main() -> None:
    agent = Agent(config=configuration)
    messages = [Message(role=Role.USER, content="What time is it?")]
    response = await agent.run(messages, stream=False)
    print(response.message.content)
    print(f"Tokens: {response.usage.input_tokens} in, {response.usage.output_tokens} out")


asyncio.run(main())
```

Streaming yields SSE-formatted events (`content`, `reasoning`, `tool_call`,
`tool_result`, `heartbeat`, `done`, ...) from the same `run()` call with
`stream=True`. For multi-turn workloads, reuse connections and sticky fallback
state with `async with agent.session() as session: ...`.

Try agents interactively with the built-in playground:

```bash
uv run neosian playground examples/basic_agent.py
```

## Providers

API keys are read from environment variables; a provider is available when its
key is set.

| Provider | Env var | Notes |
|---|---|---|
| Groq | `GROQ_API_KEY` | Default provider; also powers guardrail policy checks |
| OpenAI | `OPENAI_API_KEY` | GPT-5 family (reasoning models) |
| Anthropic | `ANTHROPIC_API_KEY` | Claude; vision/PDF input, prompt caching, adaptive thinking |
| Cerebras | `CEREBRAS_API_KEY` | |

The full model registry lives in the `Model` enum, with per-model capabilities
(`context_window`, `max_output_tokens`, reasoning/vision/document support)
declared in `ModelSpec`.

## Features

- `@Tool` decorator with JSON-Schema generation from Python signatures
  (`Literal`, `TypedDict`, `Annotated` constraints, enums)
- Parallel tool execution with per-agent concurrency caps
- Model fallback: capability-aware, sticky within a session
- Guardrails: policy-based input/output checks running concurrently with the agent
- Structured output (`ResponseFormat` with Pydantic models or unions) on all providers
- Multimodal content blocks (images, documents) on Anthropic
- Playbooks and a Blackboard for app-supplied procedures and shared state
- Evaluation harness: YAML-defined eval matrices with mocked tools and Rich reports

## Development

```bash
make install    # uv sync --locked --all-groups
make lint       # ruff + black --check + import-linter
make typecheck  # mypy --strict neosian tests
make test       # unit tier — zero API keys
make test-external provider=groq file=~/path/to/creds   # real API calls
```

See [VISION.md](VISION.md) for where the library is headed (memory and context
engineering).
