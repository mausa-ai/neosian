<img src="branding/logo-adaptive.svg" alt="neosian" width="280">

# neosian

Async-only Python library for LLM agents: tools, orchestration, streaming,
fallback, guardrails, structured output — over a stateless core, with
conversation history, cross-session memory, compaction, Postgres, and an MCP
memory server as opt-in layers.

Define tools as decorated functions, configure an agent, and run it —
blocking or streaming — with parallel tool execution, capability-aware model
fallback, guardrails, structured output, and prompt caching handled for you.
The `Agent` itself stores nothing between calls; `Conversation` wraps it when
you want a durable, memory-bearing thread.

## Install

Requires Python >= 3.12. The project is managed with
[uv](https://docs.astral.sh/uv/):

```bash
uv sync --all-groups          # library + dev tools
uv run neosian version        # CLI sanity check
```

The repository is private; as a dependency of another uv project, install
from the git URL, pinned to a release tag (extras ride the same URL):

```bash
uv add "neosian @ git+ssh://git@github.com/neosae/neosian@v0.83.1"
uv add "neosian[postgres] @ git+ssh://git@github.com/neosae/neosian@v0.83.1"   # + PostgresStore
uv add "neosian[mcp] @ git+ssh://git@github.com/neosae/neosian@v0.83.1"        # + MCP memory server
uv add "neosian[otel] @ git+ssh://git@github.com/neosae/neosian@v0.83.1"       # + OpenTelemetry spans
uv add "neosian[server] @ git+ssh://git@github.com/neosae/neosian@v0.83.1"     # + the state process
```

The core install is database-driver-free, MCP-free, and server-free
(`RemoteStore`, the state process's client, rides the core install); the
three provider SDKs (openai, anthropic, cerebras) come unconditionally —
a provider is *available* when its API key is set.

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
    model=Model.CEREBRAS_GPT_OSS_120B,
)


async def main() -> None:
    agent = Agent(config=configuration)
    messages = [Message(role=Role.USER, content="What time is it?")]
    response = await agent.run(messages, stream=False)
    print(response.message.content)


asyncio.run(main())
```

For multi-turn workloads, reuse connections and sticky fallback state with
`async with agent.session() as session: ...`. See
[examples/basic_agent.py](examples/basic_agent.py) for guardrails and
reasoning effort.

## Conversation

A store plus a conversation id turns the stateless agent into a durable
thread — history persists per turn, and resume is constructing again with
the same id:

```python
from neosian import Conversation, FileStore

store = FileStore(".neosian")
convo = Conversation(configuration, store=store, conversation_id="thread-829",
                     memory_scope="user:1234")
async with convo:
    response = await convo.send("Where did we leave off?")
```

`Conversation` accepts an `Agent` or an `AgentConfig`. Log-projection
compaction is default-on (`CompactionConfig`: 8 hot turns, trigger at 0.75
of the model's context window): aged turns are projected to one-line log
entries and a built-in `recall_turn` tool re-hydrates any of them verbatim —
compaction is paging, not deletion, and never changes what is stored.

Reflection is default-on the same way: a memory-bearing `aclose()` (the
`async with` exit above) distills what the session's turns are worth
keeping into deliberate memory writes — dedup-disciplined, audited under
the conversation id, secrets kept out — and returns a `ReflectionResult`
listing every write with the model spend. `Conversation.reflect()` is the
explicit form; `ReflectionConfig(enabled=False)` turns the close rider off
(do this in hosts that build a Conversation per request, and call
`reflect()` at the real session boundary instead). Full tour (resume,
memory scope) in
[examples/conversation_example.py](examples/conversation_example.py).

## Memory

Agent-curated, file-school memory: small markdown documents with
frontmatter plus an index injected once per conversation — no embeddings,
no vector store. At scale the index pages instead of growing: past its
budget the least recently updated documents fold into per-directory
count lines the agent re-hydrates with `view`. The agent reads and
writes through one `memory` tool
carrying six commands (`view`, `create`, `str_replace`, `insert`, `delete`,
`rename`) over mounted scopes:

```python
from neosian import AgentConfig, FileStore, MemoryConfig, Mount

configuration = AgentConfig(
    ...,
    memory=MemoryConfig(
        store=FileStore(".neosian/memory"),
        mounts=(
            Mount(scope="user:demo", mount_path="user",
                  description="durable facts about the user"),
            Mount(scope="user:demo/proj:erp", mount_path="project",
                  description="facts about the current project"),
        ),
    ),
)
```

`memory_scope="user:1234"` on `Conversation` is the one-mount sugar for
exactly this. Two write controls, enforced in code: `read_only=True`
makes a mount reference material, and `edit_only=True` fixes its
document set — existing documents stay editable, nothing may be
created, deleted, or renamed (pre-created layouts the agent works
within; argv token `eo`). Every mutation appends a full-content
version row (actor,
timestamp) — point-in-time reads and redaction come with the store, and
a Conversation's in-run writes stamp `<conversation_id>#<turn>` so every
fact carries the turn that wrote it. The
full surface (scope grammar, index generation, version history) lives on
`neosian.memory`; dogfood it with
[examples/memory_agent.py](examples/memory_agent.py).

**Writes are host-visible with undo.** On a streamed run every
successful mutation emits a `memory_write` frame right after its
`tool_result` — `{command, path, version, tool_call_id}`, never the
content — so a host can render "remembered X" with a working revert:

```python
from neosian import revert_memory

result = await revert_memory(memory_config, "/user/prefs", version=3,
                             actor="host:undo")
```

The revert executes the inverse through the same dispatcher and appends
its own version row (audit intact); if the document has already moved
past the target it refuses instead of blind-restoring. The undo route in
[examples/fastapi_chatbot.py](examples/fastapi_chatbot.py) is the
reference wiring.

## Memory from the shell

The same six commands, no Python in the loop — the shell transport over
the same dispatcher the four runtime transports execute:

```bash
neosian memory view / --root .neosian/memory --scope user:me
neosian memory create /memories/prefs.md --content - <<'EOF'
User prefers concise answers.
EOF
```

Store flags on every command: `--root` / `--scope` / `--mount
scope=...,path=...[,ro|,eo]` / `--actor` (recorded on version rows;
convention `cli:<host>`). `--json` prints the memory tool's result
envelope verbatim. Exit tiers everywhere: 0 success · 1 ran-and-failed ·
2 bad invocation · 130 interrupt; stdout carries the artifact, stderr
the `error:`/`hint:` guidance. Postgres arrives only via
`NEOSIAN_POSTGRES_DSN` (never an argv flag); `python -m neosian.memory`
is the PATH-free twin. One writer per FileStore root — see
`neosian docs topology`.

`neosian memory maintain` is the gardener: an explicit consolidation
pass over the writable mounts — keyless by default (prune empty
documents, merge byte-identical duplicates keeping the oldest),
`--model MODEL` adds the semantic pass (merge overlapping, prune stale,
promote durable user facts out of project mounts). Recently updated
documents (`--min-age-days`, default 7) are never deleted, redacted
documents are never touched, and every action is an audited version
row; model spend rides the result. The library form is
`run_maintenance(config, ...) -> MaintenanceResult`.

The operator verbs complete the governance loop, keylessly:
`neosian memory versions PATH` reads a document's audit trail
(`--json` carries full historical content — the point-in-time read);
`redact PATH` clears content everywhere while keeping the audit
skeleton (a mount root needs the explicit `--all`; redaction is the
one irreversible act); `revert PATH --version N` undoes the newest
write by appending its inverse — the shell face of `revert_memory`.

## Docs for agents

The docs travel in the wheel, version-true by construction:
`neosian docs` lists the shipped topics (quickstart, memory, cli, mcp,
topology) and `neosian docs <topic>` prints one, pipe-safe. `llms.txt`
at the repo root (and in the package, byte-identical) is the discovery
door for a coding agent with shell access alone.

## Storage

`MemoryStore` and `ConversationStore` are the contracts: async ABCs that
own no connection, commit no transaction, issue no DDL. `FileStore` (a
plain directory) and `PostgresStore` (the `postgres` extra) implement both,
and `RemoteStore` implements both over the state process's HTTP wire (see
the next section — core install, no extra); a host may implement its own,
kept honest by the shipped `MemoryStoreContract` /
`ConversationStoreContract` conformance kits.

Postgres schema application is the operator's explicit act:

```bash
python -m neosian.schemas postgres | psql "$DSN"
```

One `PostgresStore` serves many worker processes — optimistic concurrency
keeps concurrent workers on one conversation gapless. The multi-tenant
FastAPI reference (per-tenant mounts, SSE relay) is
[examples/fastapi_chatbot.py](examples/fastapi_chatbot.py).

## MCP memory server

The same stores, served to any MCP client — Claude Code, Claude Desktop,
Cursor — over stdio (`mcp` extra):

```bash
python -m neosian.mcp --root ~/.my-agent/memory --scope user:me
```

`--mount` adds scopes (read-only and edit-only supported); Postgres comes from
`NEOSIAN_POSTGRES_DSN` (never an argv flag — argv is world-readable).
The server's instructions carry the same memory index and prompt pack the
function tool uses. Hosts that embed the server in their own transport use
`create_memory_server` from `neosian.mcp`.

Registering a client is one command:
`neosian mcp install --client claude-code|claude-desktop|cursor|codex` prints
the exact `mcpServers` entry (paste-able JSON on stdout); `--write`
merges it into the client's config, preserving every other key, and
refuses a client whose config directory does not exist.

The record half, for the agent you already use: `neosian record install
--client claude-code|codex` prints the three hooks that call `neosian
record`; a prompt-to-stop span then lands as one turn by
`claude-code:<session_id>` (or `codex:<thread_id>`) plus a sessions
document, and `neosian audit --conversation <id>` reads it back
(`neosian docs agents`).

## The state process

`neosian serve` (the `server` extra) puts memory **and** conversations on
a network port, so state is reachable from another process, another app,
or another language — the multi-writer answer for a FileStore root, and
one container in a dev compose. It adds no capability the library lacks,
only reach; the quickstart above stays embedded.

```bash
NEOSIAN_SERVE_TOKEN=change-me neosian serve --root ~/.my-agent/state
```

Or as the appliance — one token, one volume, one health check
(`docker build -t neosian .` with the shipped Dockerfile):

```bash
docker run -d -e NEOSIAN_SERVE_TOKEN=change-me \
  -p 6367:6367 -v neosian-state:/data neosian
```

Python clients connect with `RemoteStore` (core install — no extra),
which implements both storage ABCs over the wire and drops in wherever
`FileStore` does; the capabilities handshake transmits the backend's
concurrency honestly:

```python
from neosian import RemoteStore

store = await RemoteStore.connect("http://localhost:6367", token="change-me")
```

Agents speak MCP over streamable HTTP at `/mcp` when the server is
started with mounts. Auth is bearer tokens, env-only — one token, or a
per-client table (`claude-code:laptop=…,app:kit=…`) so the process
records *who* wrote (an unset token refuses to start); `/health` is the
one unauthenticated route; TLS terminates at a reverse proxy.
`neosian audit --scope S` reads the ledger back on any substrate. FileStore and Postgres backends; both
conformance kits run against the served wire in CI — including against
the container, on both backends.

## Streaming and events

`run(stream=True)` returns an `AsyncIterator[AgentEvent]` — ten frozen,
`match`-able dataclasses (`ReadyEvent`, `ContentEvent`, `ReasoningEvent`,
`ToolCallEvent`, `ToolResultEvent`, `ToolProgressEvent`,
`MemoryWriteEvent`, `BlockedEvent`, `DoneEvent`, `ErrorEvent`),
sequence-stamped from 1, terminals carrying
summed usage plus per-model splits:

```python
async for event in await agent.run(messages, stream=True):
    match event:
        case ContentEvent(content=text):
            print(text, end="")
        case DoneEvent(usage=usage) if usage:
            print(f"\n[{usage.input_tokens} in, {usage.output_tokens} out]")
```

A relaying host converts events with `sse_stream()` and owns its own
keepalive and error frames (the error frame carries a machine code, never
message text); `python -m neosian.schemas events` exports the wire shapes
as JSON Schema. The reference relay is in
[examples/fastapi_chatbot.py](examples/fastapi_chatbot.py).

## Providers

API keys are read from environment variables; a provider is available when
its key is set (asking for an unavailable model raises
`MissingAPIKeyError`).

| Provider | Env var | Notes |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | GPT-5 family (reasoning models) |
| Anthropic | `ANTHROPIC_API_KEY` | Claude; vision/PDF input, prompt caching, adaptive thinking |
| Cerebras | `CEREBRAS_API_KEY` | Default provider (gpt-oss-120b) |
| xAI | `XAI_API_KEY` | `grok-4.6` — a shipped door row (`from neosian.catalog import GROK_4_6`) |
| Google Gemini API | `GEMINI_API_KEY` | `gemini-3.7-flash` on the OpenAI-compatible endpoint — a shipped door row (`GEMINI_3_7_FLASH`) |
| Fake | — | Keyless, deterministic, always available (`Model.FAKE`) |
| A registered door | the door's `api_key_env` | Any OpenAI-compatible endpoint (a fine-tune, a local server, a provider not shipped) via `register_model` |

The shipped registry lives in the `Model` enum, with per-model
capabilities (`context_window`, `max_output_tokens`, reasoning/vision/
document support) declared in `ModelSpec`. Models the enum lacks enter
through the OpenAI-compatible door — a frozen `OpenAICompatible`
(endpoint, key env var, dialect quirks) and one `register_model` call at
import:

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

A registered model is `Model`'s structural twin (`AnyModel = Model |
RegisteredModel`): cost in µ$, the context policy, capability-aware
fallback and the session's client cache treat it like a shipped one, and
every error names the door, never the shared row. Registered pricing never
enters `PRICES_FINGERPRINT`; the shipped door rows in `neosian.catalog` do —
they are first-party rows without a client of their own, each earned by
green dispatched runs of the memory baselines (DESIGN §19.5, §19.7).
`examples/custom_model_agent.py` is the runnable form.

## Running without keys

Keyless boot is an invariant: the whole unit tier, the playground on
`Model.FAKE`, and the shipped eval baseline run with zero third-party
accounts. `neosian.fake` (facade-only, not in the root namespace) ships
`FakeClient`, `FakeScript`, `FakeTurn` — scripted responses, tool calls,
assertable token counts, failure injection — injected through the
`client_factory` seam:

```python
from neosian import Agent, AgentConfig, Model
from neosian.fake import FakeClient, FakeScript, FakeTurn

fake = FakeClient(FakeScript(turns=(FakeTurn(content="scripted"),)))
agent = Agent(AgentConfig(..., model=Model.FAKE, client_factory=lambda _: fake))
```

## Hooks

`AgentHooks` is the observation seam: `on_turn`, `on_llm_call`, `on_tool`,
`on_fallback` — each sync or async, each taking one frozen event, never
altering the run (exceptions are swallowed unless `strict=True`).
`LlmCallEvent` fires after every provider call, success or failure, with
usage, duration, and `error_code` — it maps 1:1 onto a host's metering.

With the `otel` extra, `neosian.otel.otel_hooks()` returns an
`AgentHooks` that records one OpenTelemetry span per event (gen_ai
semantic-convention attributes; models, token counts, and outcomes —
never message content or tool arguments) through your tracer provider:
`AgentConfig(hooks=otel_hooks())`. The extra is `opentelemetry-api`
only; the SDK and exporter stay your choice.

## Tool approval gate

Hooks observe; the gate intercepts. `AgentConfig(tool_gate=
ToolGateConfig(approver=...))` routes every tool call — builtins
included — through one sync-or-async approver before it executes:

```python
from neosian import ToolApprovalRequest, ToolDecision, ToolGateConfig

async def approver(request: ToolApprovalRequest) -> ToolDecision:
    if request.name == "delete_records":
        approved = await ask_a_human(request)   # your channel
        return ToolDecision(approved=approved, reason="operator ruling")
    return ToolDecision(approved=True)

config = AgentConfig(..., tool_gate=ToolGateConfig(approver=approver))
```

An instant approve is no pause. A denial comes back to the model as an
ordinary failed tool result (the reason included), so the run continues
and adapts; on the streaming path a pending approval keeps emitting
`tool_progress`, and the outcome rides `tool_result` — no new wire
events. No decision is a denial, always: a timeout (default 60 s,
`timeout_seconds=None` waits indefinitely), an approver exception, or a
malformed return all deny, naming the cause. There is deliberately no
fail-open option.

## Evaluation

`neosian.evaluation` runs YAML suites and exits nonzero on failure, so
`neosian eval suite.yaml` is a CI gate. Two kinds: the default agent suite
over a variants × models × cases matrix (typed matchers, stub-by-default
tools behind an execute allowlist), and `kind: memory`, which scores store
truth across scripted sessions — write discipline, recall in the next
session, dedup, contradiction handling, long-horizon recall, correcting a
wrong memory, reflection at the close, and maintenance over a seeded
store — with a transports axis (the shipped pack runs
`transports: [function, cli, http]`; Anthropic externally adds
`native_memory` — one definition, five transports). Shipped packs:
[examples/eval_basic_agent.yaml](examples/eval_basic_agent.yaml) and
[examples/eval_memory_baseline.yaml](examples/eval_memory_baseline.yaml) —
the memory baseline is all-green on `models: [fake]`, so a red run is a
regression. The same pack, scriptless against the real providers, produces
the published numbers in [BASELINES.md](docs/BASELINES.md) — fingerprint-gated,
so a prompt-pack change without a recorded re-run fails `make test`.

## Also in the box

- `@Tool` decorator with JSON-Schema generation from Python signatures
  (`Literal`, `TypedDict`, `Annotated` constraints, enums)
- Parallel tool execution with per-agent concurrency caps
- Model fallback: capability-aware, sticky within a session
- Guardrails: policy-based input/output checks running concurrently with
  the agent (policy model configurable; defaults to the agent's own)
- Structured output (`ResponseFormat` with Pydantic models or unions) on all providers
- Multimodal content blocks (images, documents) on Anthropic; native
  `memory_20250818` and server-side compaction behind flags
- Skills and a Blackboard for app-supplied procedures and shared state
- Playground: `uv run neosian playground examples/basic_agent.py` — chats
  persist per turn to `.neosian/conversations/<id>/`, `--resume <id>`
  continues one

## Stability

`v1.0.0` is deliberately not yet cut. When it is, it will carry the API
stability promise: `Agent`, `Conversation`, `MemoryStore`, and the
`neosian.evaluation` facade stable under SemVer, the
[ECOSYSTEM.md](docs/ECOSYSTEM.md) seams (scope grammar, token classes, integer
micro-USD, event vocabulary, error codes) SemVer-guaranteed — a seam break
only at a major — and the state process's wire (the twelve `/v1/` store
routes and their envelope, versioned by `WIRE_VERSION`) stable under the
same promise: one promise covering library, seams, and wire (DESIGN
§18.1). Until then the seams are append-only by convention, and error
codes are already append-only forever. Consumers pin an annotated
`v<X.Y.Z>` tag, never master.

## Development

```bash
make install    # uv sync --locked --all-groups
make lint       # ruff + black --check + import-linter
make typecheck  # mypy --strict neosian tests
make test       # unit tier — the default gate, zero API keys
make size       # file-size gate (warn 300 / fail 500)
make test-external provider=anthropic file=~/path/to/creds  # real API calls
make test-postgres                                      # needs NEOSIAN_TEST_POSTGRES_DSN
make test-container                                     # both kits vs the container (docker)
```

The default gate needs no accounts; external tiers inject credentials
value-blind.

## Documents

- [VISION.md](docs/VISION.md) — why and what; the school we chose.
- [ROADMAP.md](docs/ROADMAP.md) — in what order; the session log.
- [DESIGN.md](docs/DESIGN.md) — how; contracts; the decisions ledger.
- [ECOSYSTEM.md](docs/ECOSYSTEM.md) — the frozen host-facing seams.
- [BASELINES.md](docs/BASELINES.md) — the published per-provider memory
  baselines: methodology, fingerprints, results.
- [SERVICES.md](docs/SERVICES.md) — every env key and what turning it off means.
- [docs/tour/](docs/tour/README.md) — the demo tour: real-key transcripts of
  every beat above, one page each, verbatim.
- [llms.txt](llms.txt) — the machine-readable front door (byte-identical
  twin ships in the wheel).
