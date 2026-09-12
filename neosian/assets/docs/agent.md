---
title: "Agent: the stateless core and every knob on it"
summary: AgentConfig field by field, the client seam, hooks vs wire events, the knobs
---

# The agent

`Agent(config)` is stateless: the caller owns history, nothing persists
between calls, and every knob lives on `AgentConfig` — a one-file agent
definition exports a `configuration` of this type and the shell, the
eval harness and a `Conversation` all read the same fields. This page
is the reference for those fields and for the seams around them; the
quickstart (`neosian docs quickstart`) is the tour.

## AgentConfig, field by field

| field | default | what it binds |
|---|---|---|
| `system_prompt: str` | required | The system prompt. A plain string — `load_prompt(path)` reads one from YAML. |
| `tools` | `[]` | `@Tool`-decorated async functions; an MCP server's `[*server.tools]` (`neosian docs mcp`). Two tools cannot share a name. |
| `model: AnyModel \| str` | `Model.CEREBRAS_GPT_OSS_120B` | A shipped `Model`, a `register_model(...)` door, or either's wire id as a string — resolved once at construction; an unknown id raises `InvalidModelError`. |
| `fallback` | `None` | `FallbackConfig(model, retry_main_after)` — capability-aware, sticky within a session. |
| `enable_todo` | `True` | The builtin `update_todo` tool. |
| `guardrails` | `None` | `GuardrailsConfig` — below. |
| `reasoning_effort` | `None` | `ReasoningEffort` on models that support it; refused otherwise. |
| `max_output_tokens` | the model's default | Output cap per completion, bounded by the model. |
| `max_parallel_tools` | `10` | Tool calls executed concurrently per batch. |
| `max_retries` | `2` | Transport-level SDK retries (429/5xx/connection). |
| `max_tool_iterations` | `10` | Tool rounds before the toolless final call; that response says `iterations_exhausted=True`. |
| `timeout_seconds` | `None` | Per-request deadline handed to the provider SDK; `None` keeps each SDK's own default. |
| `cache_conversation` | `True` | Anthropic cache breakpoint on the last message; off for one-shot calls. |
| `skill_dir` | `None` | Directory skills (`neosian docs skills`). |
| `memory` | `None` | `MemoryConfig` — the memory tool over mounts (`neosian docs memory`). |
| `client_factory` | `None` | The client seam — below. |
| `hooks` | `None` | `AgentHooks` — the observe-only callbacks, below. |
| `context_policy` | `ContextPolicy()` | The pre-call window check; `None` disables it. `ContextPolicy(estimator=...)` swaps the character heuristic for your own count. |
| `native_memory` | `False` | Anthropic's `memory_20250818` declaration for the memory tool; inert elsewhere. |
| `server_compaction` | `False` | Anthropic server-side compaction, per call. |
| `tool_gate` | `None` | `ToolGateConfig` — the approval gate every tool call passes through. |

Validation is eager: an unsupported `reasoning_effort`, an output cap
over the model's, a non-positive bound or deadline raise at
construction, never mid-run.

## The client seam

`ClientFactory = Callable[[AnyModel], BaseLLMClient]`. Set
`client_factory` and the agent asks it for a client instead of the
router — the factory receives the model a call is about to use (a
registered door is told apart by `.door`), and everything above the
client applies unchanged: fallback, guardrails, hooks, the tool gate,
conversations. A host implements `BaseLLMClient` (two methods,
`complete` and `stream`, returning `CompletionResponse` and
`StreamChunk`; tools arrive as `ToolDefinition`) to bring its own
transport; tests inject `neosian.fake.FakeClient` the same way. The
five names are root exports.

## Hooks and wire events

Two `*Event` families share the namespace. **Hook events** —
`TurnEvent`, `LlmCallEvent`, `ToolEvent`, `FallbackEvent` — arrive at
`AgentHooks` callbacks inline; they observe a run and cannot alter it
(the shipped OTel exporter is one such consumer). **Wire events** —
every `AgentEvent`: `ReadyEvent`, `ContentEvent`, `ReasoningEvent`,
`ToolCallEvent`, `ToolResultEvent`, `ToolProgressEvent`,
`MemoryWriteEvent`, `BlockedEvent`, `DoneEvent`, `ErrorEvent` — are
what `run(stream=True)` yields, the frozen wire contract a host relays
over SSE (`sse_stream`, `event_schemas`). `DoneEvent` and
`AgentResponse` both carry `iterations_exhausted`.

## The approval gate

Hooks observe; the gate intercepts. `AgentConfig(tool_gate=
ToolGateConfig(approver=...))` routes every tool call — builtins
included — through one sync-or-async approver before it executes. An
instant approve is no pause; a denial comes back to the model as an
ordinary failed tool result carrying the reason, so the run continues
and adapts; on the streaming path a pending approval keeps emitting
`tool_progress` and the outcome rides `tool_result` — no new wire
events. No decision is a denial, always: a timeout (default 60 s;
`timeout_seconds=None` waits), an approver exception, or a malformed
return all deny, naming the cause. There is no fail-open option.

## Guardrails

`GuardrailsConfig(input_mode, input_policy, block_on_input,
output_mode, output_policy, block_on_output, error_policy,
timeout_seconds, model)` runs the shipped classifier prompt on `model`
(default: the agent's own). A flagged input is blanked
(`block_on_input`, default on); a flagged output is blanked the same
way (`block_on_output`, default on — off returns the text verbatim,
flagged, for the host to handle). `timeout_seconds` bounds the
classifier call; its expiry, like any classifier error, is decided by
`error_policy`: `FAIL_OPEN` passes, `FAIL_CLOSED` blocks. The
classifier's spend always reaches `usage` — a verdict that could not be
parsed was still billed. Output guardrails need `stream=False`.

## Tool results and the `tool_` codes

A tool returns `ToolResult.ok(data)` or `ToolResult.fail(error)`. When
the failure is the library's to name, `code` carries a machine code
from `ERROR_CODES`'s `tool_` family in-band — `tool_invalid_arguments`
(the call did not bind), `tool_execution_failed` (the body raised) —
and the JSON the model sees includes it, so a bad call can be repaired
on the next turn. `tool_mcp_connection_failed` is the one raised code
of the family (`McpConnectionError`).

## Errors

Every exception carries `code`, `retryable` and a JSON-safe `details`
dict of its structural arguments — provider and status on a
`ProviderError`, the path on a load error, the window and estimate on a
`ContextWindowExceededError`. Rejected credentials are
`AuthenticationError` (`llm_authentication_failed`): a `ProviderError`,
never retryable, still fallback-eligible. `python -m neosian.schemas
errors` prints the registry.

## Provider extras

`Message.extra` is the opaque provider channel, the twin of
`ToolCall.extra`: whatever a wire must see again on the next turn and no
field names — Anthropic keeps a turn's thinking blocks with their
signatures under `extra["anthropic"]`, so a reasoning turn that called
tools replays whole. The codec persists it; only the wire that wrote it
reads it.
