---
title: "Agent: the stateless core and every knob on it"
summary: AgentConfig field by field, the client seam, hooks vs wire events, the knobs
---

# The agent

`Agent(config)` is stateless: the caller owns history, nothing persists
between calls, and every knob lives on `AgentConfig`: a one-file agent
definition exports a `configuration` of this type and the shell, the
eval harness and a `Conversation` all read the same fields. This page
is the reference for those fields and for the seams around them; the
quickstart (`neosian docs quickstart`) is the tour.

## AgentConfig, field by field

| field | default | what it binds |
|---|---|---|
| `system_prompt: str` | required | The system prompt. A plain string; `load_prompt(path)` reads one from YAML. |
| `tools` | `[]` | `@Tool`-decorated async functions; an MCP server's `[*server.tools]` (`neosian docs mcp`). Two tools cannot share a name. One run narrows them with `run(tools=…)`: below. |
| `model: AnyModel \| str` | `Model.CEREBRAS_GPT_OSS_120B` | A shipped `Model`, a `register_model(...)` door, or either's wire id as a string, resolved once at construction; an unknown id raises `InvalidModelError`. |
| `fallback` | `None` | `FallbackConfig(model=…)` or `FallbackConfig(models=[…])`: one rung or a ladder, capability-aware and sticky within a session. Below. |
| `enable_todo` | `True` | The builtin `update_todo` tool. |
| `guardrails` | `None` | `GuardrailsConfig`: below. |
| `reasoning_effort` | `None` | `ReasoningEffort` on models that support it; refused otherwise. |
| `max_output_tokens` | the model's default | Output cap per completion, bounded by the model. |
| `max_parallel_tools` | `10` | Tool calls executed concurrently per batch. |
| `max_retries` | `2` | Transport-level SDK retries (429/5xx/connection). |
| `max_tool_iterations` | `10` | Tool rounds before the toolless final call; that response says `iterations_exhausted=True`. |
| `timeout_seconds` | `None` | Per-request deadline handed to the provider SDK; `None` keeps each SDK's own default. |
| `max_cost_micro_usd` | `None` | The run's spend ceiling in integer micro-USD. Below. |
| `max_total_tokens` | `None` | The run's token ceiling, all four token classes. Below. |
| `cache_conversation` | `True` | Anthropic cache breakpoint on the last message; off for one-shot calls. |
| `stream_tool_arguments` | `False` | Relay each piece of a tool call's arguments as a `tool_call_delta` frame. Below. |
| `skill_dir` | `None` | Directory skills (`neosian docs skills`). |
| `memory` | `None` | `MemoryConfig`: the memory tool over mounts (`neosian docs memory`). |
| `client_factory` | `None` | The client seam: below. |
| `hooks` | `None` | `AgentHooks`: the observe-only callbacks, below. |
| `context_policy` | `ContextPolicy()` | The pre-call window check; `None` disables it. `ContextPolicy(estimator=...)` swaps the character heuristic for your own count. |
| `native_memory` | `False` | Anthropic's `memory_20250818` declaration for the memory tool; inert elsewhere. |
| `server_compaction` | `False` | Anthropic server-side compaction, per call. |
| `tool_gate` | `None` | `ToolGateConfig`: the approval gate every tool call passes through. |

Validation is eager: an unsupported `reasoning_effort`, an output cap
over the model's, a non-positive bound or deadline raise at
construction, never mid-run.

## The budget stop

Two ceilings bound what one run may bill:

```python
config = AgentConfig(
    system_prompt="You are helpful.",
    max_cost_micro_usd=500_000,   # $0.50, integer micro-USD
    max_total_tokens=200_000,
)
```

Both default to `None`, which is off. A run that crosses either raises
`BudgetExceededError` (`agent_budget_exceeded`, never retryable) with
`kind`, `limit` and `spent` in `details` and the billed
`usage`/`usage_by_model` on the exception, exactly like every other
terminal error. Nothing is billed past the cap: the run stops on the
call that crossed it, and a crossed budget never buys a fallback
attempt, because the ceiling is the run's and no other model can fix it.

The check sits on the run's usage ledger, which is where every billed
call is folded, so it covers both `stream=True` and `stream=False`,
every fallback rung, and the guardrail classifier's own call.

**One limit worth knowing.** `max_cost_micro_usd` prices each call
through the model's published rates, and a model registered without
pricing has none, so its spend cannot count against the cost ceiling.
The run says so once, at `WARNING`. `max_total_tokens` counts tokens
rather than money and fires on every model, priced or not.

## The fallback ladder

`FallbackConfig` takes either one rung or several, never both:

```python
FallbackConfig(model=Model.GPT_5_6_SOL)                    # one rung
FallbackConfig(models=[Model.GPT_5_6_SOL,                  # a ladder
                       Model.CEREBRAS_GPT_OSS_120B],
               retry_main_after=5)
```

A run tries the main model, then each rung in order, until one answers.
After construction `models` is always the ordered tuple and `model` is
its first rung, so a one-rung ladder behaves exactly as a single
fallback always did.

Within a session the ladder is sticky: the rung that answered is where
the next run starts, and a sticky rung that fails keeps walking down
before the main model gets a last try. `retry_main_after` successful
fallback calls return the run to the main model.

Capability gating applies per rung. A rung that cannot carry the
conversation's media leaves the ladder rather than being attempted:
media is never downgraded. A context overflow raises instead of walking
further down, because every rung below is another window the prompt
does not fit, and an overflow is the caller's error rather than
something more attempts fix.

When every rung fails, `FallbackExhaustedError.attempts` lists each
`(model, error)` in the order tried; `main_model`/`main_error` and
`fallback_model`/`fallback_error` keep naming the main model and a
fallback rung.

## Per-call tools and the choice

The configuration registers what an agent *can* call; a single run can
narrow that and say how the model must treat it:

```python
from neosian import ToolChoice

await agent.run(
    messages,
    stream=False,
    tools=["search"],                  # names, or the @Tool functions
    tool_choice=ToolChoice.required(),
)
```

`tools=None` (the default) sends every registered tool, `tools=[]` sends
none, and a name the agent does not register raises
`ConfigurationError` before any call is made: the registry is the only
name authority. Narrowing is per run, never a mutation, so the agent
keeps its whole registry for the next one.

`ToolChoice` has four shapes: `auto()` leaves the provider's default,
`required()` says a tool must be called this turn, `none()` says none
will be (the declarations still ride along, as description), and
`tool("search")` forces exactly that one. A forced tool this run does
not send, or a forced call with no tools at all, is a
`ConfigurationError`. `parallel=False` asks for at most one call per
turn where the wire has a knob for it.

Both the choice and the tools are resolved once per run and apply to
every fallback rung. The last call, the one made after
`max_tool_iterations` rounds, is the exception: it carries no tools, so
a forced choice would leave the model required to call what it was not
given, and is dropped with them.

## A schema with tools

Structured output and tools work together (they did not before 1.0):

```python
response = await agent.run(
    messages, stream=False, response_format=ResponseFormat(schema=Answer)
)
response.parsed   # an Answer
```

A model cannot be constrained to JSON by the wire while it is still
calling tools, so with tools in play the schema rides one more
declaration instead: a synthetic `final_response` tool whose arguments
are the answer's fields. The model uses the real tools as it needs them
and calls `final_response` when it is done, which ends the run the way
a text answer does. That call is the answer, not work: it never
dispatches, and `tool_results` does not list it. A registered tool
already named `final_response` is a `ConfigurationError` (rename it, or
drop `response_format`).

With no tools in play, nothing changes: the schema goes on the wire and
the reply is parsed from the text. `tool_choice=ToolChoice.none()` is
that same case, since nothing will be called.

Two shapes are still refused. `stream=True` with a schema raises
`StructuredOutputStreamingError`: validation needs the whole reply.
A schema under a `tool_choice` forcing some *other* tool raises
`StructuredOutputToolsError`, because the model is then never free to
emit the answer.

## The client seam

`ClientFactory = Callable[[AnyModel], BaseLLMClient]`. Set
`client_factory` and the agent asks it for a client instead of the
router: the factory receives the model a call is about to use (a
registered door is told apart by `.door`), and everything above the
client applies unchanged: fallback, guardrails, hooks, the tool gate,
conversations. A host implements `BaseLLMClient` (two methods,
`complete` and `stream`, returning `CompletionResponse` and
`StreamChunk`; tools arrive as `ToolDefinition`) to bring its own
transport; tests inject `neosian.fake.FakeClient` the same way. The
five names are root exports.

## Hooks and wire events

Two `*Event` families share the namespace. **Hook events**
(`TurnEvent`, `LlmCallEvent`, `ToolEvent`, `FallbackEvent`) arrive at
`AgentHooks` callbacks inline; they observe a run and cannot alter it
(the shipped OTel exporter is one such consumer). **Wire events** are
every `AgentEvent`: `ReadyEvent`, `ContentEvent`, `ReasoningEvent`,
`ToolCallEvent`, `ToolCallDeltaEvent`, `ToolResultEvent`,
`ToolProgressEvent`, `MemoryWriteEvent`, `BlockedEvent`, `DoneEvent` and
`ErrorEvent`, and
they are what `run(stream=True)` yields, the frozen wire contract a host relays
over SSE (`sse_stream`, `event_schemas`). `DoneEvent` and
`AgentResponse` both carry `iterations_exhausted`.

`ToolCallDeltaEvent` is the one frame you opt into:

```python
config = AgentConfig(system_prompt="...", stream_tool_arguments=True)
```

With it on, each piece of a tool call's arguments is relayed as it
arrives, before the finished `tool_call` frame:

```
event: tool_call_delta
data: {"event":"tool_call_delta","sequence":4,"tool_call_id":"call_1",
       "name":"weather","fragment":"{\"city\": "}
```

A fragment is a slice of JSON text and is never valid JSON on its own:
concatenate the fragments of one `tool_call_id` to get the arguments, or
just read them off the `tool_call` frame, which is unchanged and still
carries them parsed. It is off by default because it is the only frame a
turn can emit many of per call, and a host that has not asked for it
sees the stream it always saw.

## The approval gate

Hooks observe; the gate intercepts. `AgentConfig(tool_gate=
ToolGateConfig(approver=...))` routes every tool call, builtins
included, through one sync-or-async approver before it executes. An
instant approve is no pause; a denial comes back to the model as an
ordinary failed tool result carrying the reason, so the run continues
and adapts; on the streaming path a pending approval keeps emitting
`tool_progress` and the outcome rides `tool_result`, with no new wire
events. No decision is a denial, always: a timeout (default 60 s;
`timeout_seconds=None` waits), an approver exception, or a malformed
return all deny, naming the cause. There is no fail-open option.

## Guardrails

`GuardrailsConfig(input_mode, input_policy, block_on_input,
output_mode, output_policy, block_on_output, error_policy,
timeout_seconds, model)` runs the shipped classifier prompt on `model`
(default: the agent's own). A flagged input is blanked
(`block_on_input`, default on); a flagged output is blanked the same
way (`block_on_output`, default on; off returns the text verbatim,
flagged, for the host to handle). `timeout_seconds` bounds the
classifier call; its expiry, like any classifier error, is decided by
`error_policy`: `FAIL_OPEN` passes, `FAIL_CLOSED` blocks. The
classifier's spend always reaches `usage`: a verdict that could not be
parsed was still billed. Output guardrails need `stream=False`.

## Tool results and the `tool_` codes

A tool returns `ToolResult.ok(data)` or `ToolResult.fail(error)`. When
the failure is the library's to name, `code` carries a machine code
from `ERROR_CODES`'s `tool_` family in-band: `tool_invalid_arguments`
(the call did not bind), `tool_execution_failed` (the body raised).
The JSON the model sees includes it, so a bad call can be repaired
on the next turn. `tool_mcp_connection_failed` is the one raised code
of the family (`McpConnectionError`).

## Errors

Every exception carries `code`, `retryable` and a JSON-safe `details`
dict of its structural arguments: provider and status on a
`ProviderError`, the path on a load error, the window and estimate on a
`ContextWindowExceededError`. Rejected credentials are
`AuthenticationError` (`llm_authentication_failed`): a `ProviderError`,
never retryable, still fallback-eligible. `python -m neosian.schemas
errors` prints the registry.

## Provider extras

`Message.extra` is the opaque provider channel, the twin of
`ToolCall.extra`: whatever a wire must see again on the next turn and no
field names. Anthropic keeps a turn's thinking blocks with their
signatures under `extra["anthropic"]`, so a reasoning turn that called
tools replays whole. The codec persists it; only the wire that wrote it
reads it.
