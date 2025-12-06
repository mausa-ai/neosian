# neosian Vision

> Capable AI agents. One-file integration. Zero magic.

## What is neosian?

neosian is a stateless agentic AI library for the neosae ecosystem. It handles LLM orchestration, tool execution, and streaming - nothing more.

**The Promise**: Define an agent in one file. The app handles the rest.

## Philosophy

- **Full Control, Full Transparency** - No magic. Every line justified.
- **Simple Elegant Quality Code** - Not one line that is not needed.
- **Stateless by Design** - App owns memory, neosian owns execution.
- **Type Safety** - mypy --strict compliance.

## The Problem with Existing Solutions

| Framework | Problem |
|-----------|---------|
| LangChain | Abstraction hell. Magic everywhere. |
| CrewAI | Prompt-play, not capable agents. |
| AutoGen | Complex multi-agent overhead for simple tasks. |
| All of them | Token waste. Every request loads all tools. |

## The neosian Difference

### 1. Stateless Agent Core

neosian doesn't own memory. It receives context, produces responses.

```python
from neosian import Agent

agent = Agent(
    name="media-creator",
    system_prompt="You create images, videos, and audio.",
    tools=[generate_image, generate_video, generate_speech],
)

# App provides history, app saves results
async for chunk in agent.chat(
    message="Create a sunset image",
    history=conversation_history,  # App provides
):
    yield chunk
```

**Why stateless?**
- App already has users, subscriptions, DynamoDB
- No data duplication
- No sync bugs
- neosian works with any storage backend

### 2. Two-Mode Tool System

**Mode 1: Fixed Tools** (for focused apps)
```python
agent = Agent(
    name="media-creator",
    tools=[generate_image, generate_video, generate_speech],
)
```
- Tools loaded directly into context
- Minimal token usage
- Perfect for single-purpose agents

**Mode 2: Tool Registry** (for powerful apps)
```python
agent = Agent(
    name="assistant",
    registry=ToolRegistry.default(),
)
```
- Agent has ONE meta-tool: `search_tools`
- Sub-agent finds relevant tools on demand
- Scales to hundreds of tools without token explosion

### 3. The Tool Registry Innovation

**The strawberry problem**: LLMs fail at "How many R's in strawberry?" because they reason instead of reaching for tools. Humans count. They use tools.

neosian agents don't try to know everything. They find the right tool.

```
User: "Count the R's in strawberry"

Agent: [needs a tool]
       → search_tools("count characters in string")
       → finds: count_characters
       → uses tool
       → "3"
```

**Why this matters:**
- Tools can be added at runtime (no redeploy)
- Token cost stays constant regardless of tool count
- Agent capabilities grow without code changes

### 4. Multi-Tool Execution

Complex tasks work within a single turn:

```
User: "Create a lecture video about quantum computing"

Agent: → research_topic("quantum computing")
       → generate_slides(content)
       → generate_narration(content)
       → compose_video(slides, audio)
       → "Here's your video: https://..."
```

**No external memory needed.** Tool results accumulate in context. The LLM orchestrates the loop.

## Architecture

```
neosian/
├── _foundation/
│   ├── llm/                    # LLM Provider Layer
│   │   ├── base.py            # BaseLLMClient protocol
│   │   ├── groq.py            # Groq (primary)
│   │   ├── openai.py          # OpenAI (fallback)
│   │   ├── anthropic.py       # Claude
│   │   └── router.py          # Provider routing & fallback
│   │
│   ├── agent/                  # Agent Core
│   │   ├── base.py            # Agent class
│   │   ├── executor.py        # Tool execution loop
│   │   └── streaming.py       # SSE chunk generation
│   │
│   ├── tools/                  # Tool System
│   │   ├── base.py            # @Tool decorator, ToolResult
│   │   ├── registry.py        # Tool registry
│   │   └── searcher.py        # Registry search sub-agent
│   │
│   ├── guardrails/            # Safety
│   │   ├── input.py           # Input validation
│   │   └── output.py          # Output sanitization
│   │
│   └── shared/
│       ├── types.py           # Type definitions
│       ├── constants.py       # Model configs, limits
│       └── exceptions.py      # Error hierarchy
│
└── py.typed
```

## Tool Definition

```python
from neosian import Tool, ToolResult

@Tool(
    name="generate_image",
    description="Generate an image from a text prompt",
)
async def generate_image(
    prompt: str,
    style: str = "realistic",
) -> ToolResult[dict]:
    result = await image_api.generate(prompt, style)
    return ToolResult.success({"url": result.url})
```

## Tool Registry

```python
from neosian import ToolRegistry

registry = ToolRegistry()

# Register with categories for semantic search
registry.register(generate_image, categories=["media", "images"])
registry.register(generate_video, categories=["media", "video"])
registry.register(count_characters, categories=["text", "analysis"])

# Add tools at runtime - no redeploy
registry.register_dynamic(
    name="new_tool",
    description="Does something new",
    endpoint="https://api.example.com/tool",
)
```

## Integration Pattern

neosian is a library. The app controls everything else.

```python
# App's chat endpoint
@router.post("/chat")
@require_auth
@rate_limit(requests=20, window=60)
async def chat(request: ChatRequest, user: User):
    # 1. Load history (app's job)
    history = await conversation_repo.get(request.conversation_id)

    # 2. Call agent (neosian's job)
    new_messages = []
    async for chunk in agent.chat(request.message, history):
        if isinstance(chunk, TextChunk):
            yield sse_format(chunk)
        elif isinstance(chunk, TurnComplete):
            new_messages = chunk.messages

    # 3. Save results (app's job)
    await conversation_repo.save(request.conversation_id, new_messages)
```

## Separation of Concerns

| Concern | neosian | App (neosae-base) |
|---------|---------|-------------------|
| LLM calls | ✓ | |
| Tool execution | ✓ | |
| Tool registry | ✓ | |
| Streaming | ✓ | |
| Guardrails | ✓ | |
| Conversation storage | | ✓ |
| User context | | ✓ |
| Authentication | | ✓ |
| Rate limiting | | ✓ |
| Usage tracking | | ✓ |

## Configuration

```python
from neosian import configure

configure(
    default_model="groq:llama-3.3-70b-versatile",
    fallback_models=["openai:gpt-4o-mini"],
)
```

```bash
GROQ_API_KEY=gsk_...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

## Roadmap

### Phase 1: Core
- [ ] LLM provider layer (Groq + OpenAI)
- [ ] Agent with tool execution loop
- [ ] SSE streaming
- [ ] Basic guardrails

### Phase 2: Tool Registry
- [ ] Tool registry with semantic search
- [ ] Search sub-agent
- [ ] Runtime tool registration
- [ ] All LLM providers

### Phase 3: Polish
- [ ] Advanced guardrails
- [ ] Comprehensive tests
- [ ] Documentation

## The Vision

```
┌──────────────────────────────────────────────────┐
│                  Your App                         │
│                                                   │
│  ┌─────────────┐     ┌─────────────────────────┐ │
│  │   Memory    │     │     agent.py            │ │
│  │  (App owns) │     │     (One file)          │ │
│  └─────────────┘     └─────────────────────────┘ │
│         │                       │                │
│         └───────────┬───────────┘                │
│                     ▼                            │
│  ┌─────────────────────────────────────────────┐ │
│  │                neosian                       │ │
│  │  LLM → Tools → Streaming → Response         │ │
│  └─────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
```

**One agent. One file. Stateless execution.**