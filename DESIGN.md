# neosian — Design

> Status: **active** (2026-08-18). Precedence: [VISION.md](VISION.md) says *why
> and what*, this document says *how* (mechanisms and contracts),
> [ROADMAP.md](ROADMAP.md) says *in what order* and holds cross-session state.
> ROADMAP wins on order, DESIGN wins on mechanism.
> [ECOSYSTEM.md](ECOSYSTEM.md) is the frozen, host-facing subset of this
> document — for hosts, it wins. [CLAUDE.md](CLAUDE.md) carries the working
> conventions and [SERVICES.md](SERVICES.md) the env-key map. Contracts below
> marked **(NS)**, **(N0)**, **(N1)** land in the named roadmap phase; until
> then they are spec, not code.

## §0 Precedence and reading order

A session starts from ROADMAP.md (the ▶ pointer), reads the DESIGN sections the
phase names, and treats ECOSYSTEM.md and ROADMAP's Decided constraints as
settled — they are never relitigated inside a phase.

## §1 Package shape & layering

```
neosian/                  # the facade: __init__.py re-exports the public API
├── fake.py               # public FakeProvider surface (lazy, excludable) (NS)
├── _foundation/          # all real code: agent/ llm/ shared/ tools/
│                         #   guardrails/ blackboard/ evaluation/
│                         #   (+ memory/, N1; + conversation/, N2;
│                         #    + postgres/, N3; + mcp/, N4)
├── mcp/                  # public MCP facade + the stdio entry point (N4)
├── evaluation/           # public eval facade (lazy, dev-time) (NE, §13)
├── _cli/                 # playground, eval CLI, config
└── assets/               # data files: prompts (YAML), sql (DDL),
                          #   docs (markdown+frontmatter), llms.txt, ascii art
```

Import-linter contracts (inline in pyproject, kit idiom — the matrix is spelled
out, `forbidden` type only):

- `_foundation ↛ _cli` — the library never imports its CLI.
- `_foundation.memory` and `_foundation.postgres` ↛ provider internals
  (`_foundation.llm.<provider>` modules) — storage substrates speak only
  to ABCs.
- `_foundation.mcp` ↛ `_foundation.agent` and ↛ provider internals — the
  MCP server is store + tool layer only (N4).
- `_foundation.conversation` ↛ provider internals, with
  `allow_indirect_imports` (Conversation drives an `Agent`, which owns the
  router — ledger #26); and the conversation *storage-seam* modules (`base`,
  `types`, `ids`, `file_turns`, `testing`) plus `_foundation.postgres`
  ↛ `_foundation.agent`.
- Every runtime `_foundation` package ↛ `_foundation.evaluation` — the
  harness observes the library, never the reverse; and
  `_foundation.evaluation` ↛ the provider client modules with
  `allow_indirect_imports` (it drives an `Agent` and scripts `llm.fake` —
  its legitimate seams) (NE, §13.11).
- `_foundation.otel` ↛ provider internals and every layer beyond the hook
  seam (conversation, memory, postgres, mcp, evaluation, guardrails) —
  the exporter consumes `agent/hooks.py` events and nothing else
  (NV, ledger #83).
- The facade (`neosian/__init__.py`, `neosian/fake.py`) only re-exports; no
  logic lives there.

The public API is pinned: `tests/unit/test_init.py::test_all_list_matches_exports`
makes every `__all__` change a deliberate, reviewed diff.

## §2 Providers & the router

Three adapters (openai, anthropic, cerebras) + FakeProvider (guarantee
in ECOSYSTEM §7; public surface `neosian.fake`, NS) behind `BaseLLMClient`. Capabilities live in `ModelSpec` and describe
*neosian's converter*, not raw provider ability. Fallback is capability-aware
and sticky within a session; SDK-native retries stay at the client layer.
Provider SDK exceptions never escape neosian — `wrap_provider_error` (§5)
classifies them at the client boundary. Provider-native tool types ride the
internal `ToolDefinition.native_type` marker (N4, ledger #41): the Anthropic
converter emits the schema-less native declaration for a marked definition,
every other converter ignores the marker and sends the ordinary function
schema — so capability-aware fallback needs no new gate. Anthropic's
server-side compaction rides `server_compaction`, a per-call kwarg on the
ABC exactly like `cache_conversation` (Anthropic-only; the other clients
no-op it; ledger #45); support is the `ModelSpec.supports_compaction_blocks`
capability, never a provider check — Haiku 4.5 is Anthropic and outside the
beta's set (ledger #47) — gating both the request pre-flight and the
fallback of a compaction-bearing history.

## §3 Agent core

`Agent` is stateless: the caller owns history; nothing persists between calls.
`AgentSession` reuses connections and fallback state. Both entry points
funnel through one `Agent._dispatch` (guards unskippable by construction; a
parametrized test runs identical scenarios over both and asserts identical
raises and identical hook sequences). The `*_with_session` method twins
collapsed at N0 into a frozen per-run `RunContext` — `acquire` (the
client_factory-honoring seam: fresh client per attempt for Agent, cached
per provider for a session), sticky `fallback_state`, the hook dispatcher —
threaded through free functions in sibling modules (`blocking`, `loop`,
`stream_run`, `stream_loop`, `stream_final`, `guards`, `fallback`,
`tool_exec`, `emit`); `agent/base.py` keeps only configuration + the funnel.

Each attempt (one model + client try) owns an `Attempt`: a fresh message
snapshot (register #5) plus a usage ledger keyed by API-reported model that
outlives exceptions — terminal errors and `AgentResponse` read
usage/usage_by_model/turn_messages off it. The ledger is inherited across
fallback attempts (a failed attempt's tokens were billed); messages never
are. `AgentResponse.turn_messages` is the turn-capture contract: tuple of
provider-order messages, `turn_messages[-1] is response.message`, input +
turn_messages replays as valid history; empty on blocked responses.

Hook insertion points (landed N0): the blocking/streaming tool loops and
the max-iterations final call (`on_llm_call`, success and failure), tool
batches in submission order (`on_tool`), `_dispatch` for blocking and the
streamed done/blocked terminals (`on_turn`, exactly once per run that
yields a response — fired **before** the terminal event is yielded, so a
consumer that saw `done`/`blocked` has had the hook run; register #6), the
fallback switch sites + sticky retry-main (`on_fallback`). Hooks await
inline — sequences are deterministic; the eval runner's fallback detection
rides `on_fallback` instead of scraping logs. The shipped OTel exporter
(NV) is a hooks consumer, not a fifth insertion point:
`neosian.otel.otel_hooks()` returns a plain `AgentHooks` emitting one
flat post-hoc span per event (`otel` extra = `opentelemetry-api` only;
span content excludes messages, tool arguments, and results — ledger
#83). `Agent` exposes read-only
`config` and `max_tool_iterations` — an Agent knows its configuration,
never its history; `Conversation` (§9) derives its own config from either.

Found-bug register (fixed in NS/N0, tests pin each):
1. `AgentSession.run` skipped all three validation guards (NS).
2. `_attach_input_guard_results` rebuilt `AgentResponse` field-by-field and
   dropped `model=`; the fix is `dataclasses.replace` — manual rebuilds are how
   fields get lost (NS). `AgentResponse` is frozen+slots since N0.
3. Fallback runs summed usage across models, making per-model pricing
   impossible → `usage_by_model` (N0).
4. Usage rode on exceptions via a private attribute, streaming-only →
   public `LLMError.usage` / `.usage_by_model` on both paths (N0; the
   blocking path previously raised with no usage at all).
5. One shared message list was mutated by a failed main attempt and then
   handed to the fallback attempt, leaking partial tool rounds into the
   fallback's history → per-attempt snapshot in `Attempt` (N0).
6. The streamed paths yielded the terminal event *before* firing `on_turn`,
   so a consumer that stopped iterating at `done` silently skipped the hook
   — and would have lost the persisted turn under §9 → `emit_turn` fires
   before the terminal yield at every streamed site (N2).

## §4 Usage, pricing, cost **(NS)**

The four token classes are ecosystem vocabulary (ECOSYSTEM §3):

```python
@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0      # was cache_read_input_tokens
    cache_write_tokens: int = 0     # was cache_creation_input_tokens
```

`ModelPricing` becomes integer µ$/MTok, field order matching the kit's rate
card (input, output, cache_read, cache_write); `None` cache fields mean "no
separate published rate" and `effective_cache_{read,write}_per_mtok`
properties fall back to the input rate. A unit test asserts no shipped price
required rounding during the float→µ$ conversion.

```python
MICRO_PER_USD: Final = 1_000_000

def cost_micro_usd(self, model: Model) -> int | None:
    ...
    return -(-total // _MTOK)      # ceiling — never undercount
```

Character-identical arithmetic to the kit's `MeterService.compute_cost`; a
golden-vector test (same counts + rates → same int) proves the two houses
agree. `Usage.cost() -> float` is **dropped** (zero production call sites);
display goes through `format_micro_usd(micro, *, places=4) -> str` — a float
never re-enters the vocabulary.

`PRICES_AS_OF` (ISO date str) gains `PRICES_FINGERPRINT` (sha256 of the
canonical pricing rendering); a unit test recomputes it, so any price edit
fails CI until the date is bumped in the same commit — a gate, not a promise.

`ModelUsage(model: str, usage: Usage)` — per-API-reported-model attribution,
one entry per distinct model, first-appearance order; produced by the hook
layer (§3), aggregated onto `AgentResponse.usage_by_model` and terminal events.

## §5 Errors **(NS)**

```python
class NeosianError(Exception):
    code: ClassVar[str] = "neosian_error"
    default_retryable: ClassVar[bool] = False
    def __init__(self, message, *, details=None, retryable=None): ...
    # .message (developer English), .details (JSON-safe), .retryable
```

No `http_status` — the host owns HTTP. Codes match
`^(neosian|agent|llm|tool|guardrail|memory|prompt|playbook|blackboard|eval)_[a-z0-9_]+$`;
the closed family-prefix set makes collision with host codes mechanically
impossible. Every exception gets an explicit code (a future subclass degrades
to its family code, never to a missing key). Codes are **append-only**.

The registry (`ERROR_CODES: Mapping[str, type[NeosianError]]`, built by
recursive subclass walk, exported; `python -m neosian.schemas errors` prints
it as JSON for host i18n-coverage tests):

| Exception | code | retryable |
|---|---|---|
| NeosianError | `neosian_error` | no |
| LLMError | `llm_error` | no |
| ProviderError | `llm_provider_error` | per-instance |
| ContextWindowExceededError *(new)* | `llm_context_window_exceeded` | no |
| ModelFailedError | `llm_model_failed` | no |
| FallbackExhaustedError | `llm_fallback_exhausted` | no |
| ToolCallGenerationError | `llm_tool_call_generation_failed` | **yes** |
| FakeScriptExhaustedError *(NS)* | `llm_fake_script_exhausted` | no |
| MessageSerializationError | `llm_message_serialization_failed` | no |
| UnsupportedParameterError | `llm_unsupported_parameter` | no |
| UnsupportedContentError | `llm_unsupported_content` | no |
| ConfigurationError | `agent_configuration_error` | no |
| InvalidModelError | `agent_invalid_model` | no |
| MissingAPIKeyError | `agent_missing_api_key` | no |
| AgentLoadError family | `agent_load_failed`, `agent_file_not_found`, `agent_missing_configuration`, `agent_invalid_configuration`, `agent_invalid_definition` | no |
| StructuredOutputError family | `agent_structured_output_error`, `agent_structured_output_requires_blocking`, `agent_structured_output_incompatible_with_tools` | no |
| GuardrailError family | `guardrail_error`, `guardrail_policy_parse_failed`, `guardrail_output_requires_blocking` | no |
| PromptLoadError family | `prompt_load_failed`, `prompt_file_not_found`, `prompt_invalid_yaml`, `prompt_missing_key` | no |
| PlaybookLoadError family | `playbook_load_failed`, `playbook_file_not_found`, `playbook_invalid_frontmatter`, `playbook_missing_key`, `playbook_duplicate_name`, `playbook_directory_not_found` | no |
| BlackboardError family | `blackboard_error`, `blackboard_entry_not_found`, `blackboard_read_failed`, `blackboard_update_failed`, `blackboard_directory_not_found` | no |
| EvalError family | `eval_error`, `eval_config_not_found`, `eval_config_invalid_yaml`, `eval_config_missing_key`, `eval_prompt_not_found`, `eval_case_invalid`, `eval_run_failed`, `eval_config_unknown_key` (NE), `eval_model_unknown` (NE) | no |
| Memory family (N1) | `memory_error`, `memory_document_not_found`, `memory_scope_invalid`, `memory_path_invalid`, `memory_conflict`, `memory_format_unsupported`, `memory_read_only_mount`, `memory_edit_only_mount` (NP) | no |
| Conversation family (N2) | `agent_conversation_error`, `agent_conversation_id_invalid`, `agent_conversation_format_unsupported` | no |

The memory base class is **`MemoryStoreError`** — never `MemoryError`, which
shadows a Python builtin in `__all__`. The conversation base class is
`ConversationStoreError`; the `agent_` prefix is forced by the closed family
set (§9.10, ledger #24).

**The SDK wrap.** `ProviderError(provider, message, *, status=None,
retryable=False, request_id=None)`; new `llm/errors.py::wrap_provider_error
(provider, exc, *, model=None)` — the keyword-only `model` lets the 400
classifier build a `ContextWindowExceededError` carrying `context_window`
structurally (ECOSYSTEM §6); the two-argument form stays valid. Never wraps
a `NeosianError`; duck-typed status extraction
(`.status_code`, then `.response.status_code`); 429/408/5xx/connection/timeout
→ `retryable=True`; a 400 matching context signatures
(`context_length_exceeded`, `prompt is too long`,
`model_context_window_exceeded`, `Request too large`) →
`ContextWindowExceededError`. Every raise is `from exc`, so `__cause__` keeps
the SDK exception and traceback. Each client wraps its `complete()` and
`stream()` bodies; raw SDK exceptions no longer escape (a caller catching
`anthropic.APIStatusError` around `run()` breaks — that is the point).
`ModelFailedError`/`FallbackExhaustedError` gain `cause_code` and
`provider_status` so terminal frames carry structure, not formatted English.

**`ContextWindowExceededError(model, *, context_window=None,
estimated_tokens=None, provider=None)`** — reactive producer is the wrap;
proactive producer is N0's `ContextPolicy` (finally a consumer for
`ModelSpec.context_window`). Rule: an overflow does **not** trigger fallback
unless the fallback model's window exceeds the primary's — falling back
smaller is a guaranteed second failure and a doubled bill.

Exported: `NeosianError`, `LLMError`, `ProviderError`,
`ContextWindowExceededError`, `ConfigurationError`, `MissingAPIKeyError`,
`GuardrailError`, `ToolCallGenerationError`, `UnsupportedParameterError`,
`AgentLoadError`, `PromptLoadError`, `EvalError`, `ERROR_CODES`.

## §6 Streaming & events **(N0; spec frozen at NS via ECOSYSTEM §5)**

Frozen, slotted dataclasses — one class per event, `match`-able,
behavior-carrying (`to_dict()`, `to_sse()`); pydantic touches them only at
schema-export time (`TypeAdapter(...).json_schema()` — zero hot-path cost).

```python
EVENT_PROTOCOL_VERSION: Final = 2
AgentEventType: ready | content | reasoning | tool_call | tool_result
              | tool_progress | memory_write | blocked | done | error
```

- `ReadyEvent(protocol, requested_model, provider)`.
- `ToolProgressEvent(tool_call_id, elapsed_ms: int)` — renamed from
  `heartbeat`: it means "this tool is still running", which no keepalive
  means. Keepalive is the host's SSE comment on the host's timer (only the
  host knows its proxy's idle timeout); neosian emits none.
- `MemoryWriteEvent(tool_call_id, command, path, version, previous_path)` —
  NP's amendment (ledger #99; ECOSYSTEM §5 carries the seam text): one frame
  per successful mutating memory command, pushed in `tool_exec` immediately
  after that call's `tool_result`, built from the `MemoryWriteReceipt` the
  command attached (#98). Never content; `view`, failures and off-stream
  writes stay silent. `version` is the undo argument (`revert_memory`, §8).
- Terminal events (`DoneEvent`, `ErrorEvent`, `BlockedEvent`) carry `usage`
  (sum) + `usage_by_model: tuple[ModelUsage, ...]`.
- `DoneEvent.model` is the **API-reported** string of the final completion —
  unified with `AgentResponse.model` (enabler: `StreamChunk.model`, populated
  by every client from its first chunk / `message_start`).
- `ErrorEvent(code, retryable, usage, usage_by_model)` has **no message
  field** — it cannot leak text by construction.
  `ErrorEvent.from_exception(exc, *, sequence)` reads the public `.code`,
  `.retryable`, `.usage`, `.usage_by_model`.

`run(stream=True)` returns `AsyncIterator[AgentEvent]` — typed events, not
pre-serialized strings — and **still raises on failure**; a relaying host
converts in its own generator:

```python
try:
    async for e in agent.run(msgs, stream=True):
        yield e.to_sse()
except NeosianError as exc:
    logger.error(..., code=exc.code)                       # full text → logs
    yield ErrorEvent.from_exception(exc, sequence=seq).to_sse()  # code → wire
```

A keepalive-emitting host holds the pending `__anext__` in a persistent
task (`asyncio.wait({task}, timeout=…)`) — `wait_for(anext(it), …)`
cancels it on timeout, throwing CancelledError into the generator and
killing the stream mid-turn. `examples/fastapi_chatbot.py` is the
reference implementation of this relay, pinned by the `external_postgres`
suite.

Wire form: `event: <name>\ndata: <compact single-line JSON object>\n\n` with
`ensure_ascii=True` (escaped newlines — no model output can break framing);
byte-compatible with the kit's `_frame` and its `parseFrame`. No `id:` field —
resume is host territory. `sse_stream(events)` is the verbatim-relay adapter.
`EventSequencer.stamp()` returns `dataclasses.replace(event, sequence=n)`,
starting at 1 (the frontend owns 0). Schema export: `event_schemas()` +
`python -m neosian.schemas events [--out DIR]` — one schema per event plus a
`oneOf` root discriminated on `event`; hosts codegen their typed SSE seam from
it (the kit's OpenAPI deliberately excludes streaming shapes). The payload
TypedDicts and `event_schemas()` live in `agent/event_schemas.py` (NP — the
size gate; one contract, two modules, and the import edge stays one-way:
`event_schemas` reads `events`, never the reverse).

Removed with v2: `SSEEventType`, `SSEEvent`, `SSEEventEmitter`,
`stream_to_sse`, all eight `*_event()` builders — the dataclasses are the
constructors.

Server-compaction blocks (N4) are invisible to this vocabulary — context
bookkeeping, not user-visible content, and the event set is frozen
(ledger #48). Their spend reaches hosts through the terminal events'
usage: the Anthropic client folds the beta's `usage.iterations`
compaction entries into the `Usage` it reports (the API excludes them
from its top-level counts — hidden spend would break the money-visibility
promise). The blocks themselves ride `StreamChunk.compaction` into the
assistant message the loops build, so persistence and the echo-back
contract hold on both paths.

## §7 Prompts as data **(NS)**

What moves out of Python into `assets/` YAML (loaded + validated at import,
fail-fast; overridable at construction by registry key):

- the guardrail classifier prompt (`Guardrails.PolicyPrompt.TEMPLATE`) and
  category template;
- the six `CommonPolicies` (descriptions + example phrases);
- built-in tool descriptions (todo / playbook / blackboard — model-facing
  prose).

Error-message *templates* stay in Python (developer strings, not prompts).
The N1 memory prompt pack ships the same way from day one. Structured output
remains purely schema-driven — no prose to move.

## §8 Memory & storage ABCs **(N1; constraints frozen now)**

Seven constraints keep `MemoryStore` host-implementable:

- **C1 — async signatures, zero I/O ownership.** No `__init__` in the ABC, no
  connect/close/migrate/DDL, never begins/commits/rolls back a transaction. A
  store method must be safe inside a caller-owned transaction and must not
  assume durability on return. (The kit implements the async methods over its
  synchronous sessions via the threadpool; its DDL is its own alembic branch.)
- **C2 — read-your-writes within a task.** The memory tools check-then-write;
  without this the agent duplicates documents. This is the strongest guarantee
  the ABC may demand — durability is the host's.
- **C3 — scope-wide `redact()`.** `path=None` = the whole scope. Clears
  content on current documents **and every version row**, preserves the audit
  skeleton (paths, versions, timestamps, actor), never deletes rows. After
  `redact(scope)`: listings show `redacted=True`, reads return empty content,
  version counts are unchanged.
- **C4 — UTC-aware datetimes, injectable `Clock`.** Every returned datetime is
  tz-aware UTC; naive is a contract violation the test-kit asserts against.
- **C5 — per-document monotonic `version` from 1**, full content on version
  rows (memories are KB-scale; point-in-time reads come free). Never a global
  sequence (needs a global lock no multi-worker host can afford).
- **C6 — `MEMORY_FORMAT_VERSION = 1`** in frontmatter (`neosian_format: 1`) or
  a column; refuse to read newer (`memory_format_unsupported`); **preserve
  unknown frontmatter keys on write**.
- **C7 — no policy, no path semantics in the store.** Mounts, read-only
  enforcement, index generation, the prompt pack: Conversation/tool layer.
  `str_replace`/`insert` are tool-layer compositions of read+write.

```python
class MemoryStore(ABC):
    supports_optimistic_concurrency: ClassVar[bool] = False
    async def read(scope, path) -> MemoryDocument | None
    async def write(scope, path, content, *, actor=None, expected_version=None) -> MemoryDocument
    async def delete(scope, path, *, actor=None) -> bool
    async def rename(scope, src, dst, *, actor=None) -> MemoryDocument
    async def list_documents(scope, *, prefix="") -> tuple[MemoryEntry, ...]
    async def versions(scope, path, *, limit=50) -> tuple[MemoryVersion, ...]
    async def redact(scope, *, path=None, actor=None) -> int
```

Frozen value types `MemoryDocument` / `MemoryEntry` / `MemoryVersion`;
`actor` is the writing `conversation_id`, opaque — since NP, Conversation's
in-run tool writes stamp `<conversation_id>#<turn>` (the turn-ref, resolved
per command by a late-bound actor closure; ledger #100), while reflection
stays the bare id (#86) and CLI/MCP/eval actors keep their own spellings;
the store never parses any of them. `expected_version=` raises
`MemoryConflictError` where `supports_optimistic_concurrency` is declared.

**Scope grammar** — ECOSYSTEM §2 verbatim; `Scope` NewType, `parse_scope`,
`SCOPE_PATTERN`, `SCOPE_MAX_LENGTH = 512` exported. Neosian never parses
meaning from a scope (pinned by a test: nothing outside `memory/scope.py`
calls `scope_segments`). Document paths are extension-free logical names
(segments `[A-Za-z0-9_.-]{1,128}`, no leading `/`, no `.`/`..`, ≤ 16 segments,
≤ 512 chars); `FileStore` appends `.md`.

**The shipped conformance kit** — `neosian/memory/testing.py::
MemoryStoreContract`: subclass, provide a `store` fixture, inherit ~25 tests
(UTC-awareness, redact semantics, version monotonicity, read-your-writes,
format refusal, unknown-key preservation, validation rejections,
`expected_version`). It is the mechanism that keeps host-implemented stores
honest across repos; the kit's adapter must subclass it. Substrate-planting
tests (format refusal, unknown-key survival) ride one overridable
`plant_raw_document` hook, skipping where unimplemented; the
`expected_version`-mismatch test is gated on the ClassVar.

**Value types (fields are contract, pinned by the kit):**
`MemoryDocument(scope, path, content, version, created_at, updated_at,
actor, redacted, extra)` — `extra: Mapping[str, Any]` carries preserved
unknown storage keys, output-only (`write()` accepts none; they enter only
from the substrate). `MemoryEntry(path, version, created_at, updated_at,
redacted)`. `MemoryVersion(path, version, action, content, actor,
created_at, redacted)`; `MemoryAction = Literal["created", "modified",
"deleted"]` — redaction is deliberately not an action: it appends nothing.

**Semantic rulings (N1; each pinned by the kit):** `read` → `None` for
never-written or deleted, while redacted documents read back
`content=""`/`redacted=True`. Every mutation appends exactly one row per
path touched; a delete consumes the next version number and a re-create
continues from the maximum ever issued (resetting `created_at`); an
identical-content write still bumps. `versions()` is newest-first,
`limit` takes the most recent N (`limit=0` → `()`, unknown path → `()`),
and the newest row mirrors the live document unless its action is
`deleted`. `rename` = `deleted`@src + `created`@dst (dst continues its own
history); missing src → `memory_document_not_found`; occupied dst — src ==
dst included — → `memory_conflict` (`reason: "destination_exists"`), never
a silent overwrite. `redact` returns the count of distinct paths matched
(idempotent in effect and return value), never bumps `updated_at`;
`redacted` is per-state, never sticky — a later write yields a fresh
un-redacted document while history stays cleared. Validation is
scope-then-path on every method, no normalization anywhere. `list_documents`
excludes deleted, includes redacted, sorts by path; `prefix` is a plain
string prefix, never validated, never a raiser. `expected_version`
mismatch/absent-doc → `memory_conflict` where the ClassVar is declared; any
store must accept a matching value. FileStore declares `False` (files
cannot arbitrate between processes) yet honors the check best-effort under
its in-process lock.

**FileStore layout** — one directory per percent-encoded scope segment
(every component carries `%3A`, so no collision with reserved names, and
a 512-char scope never exceeds filesystem name limits):
`<seg>/…/documents/<path>.md` (exact-codec envelope + verbatim body, byte-
exact round-trip, content beginning with `---` included) ·
`versions/<path>.jsonl` (the version counter's source of truth; malformed
lines raise, never skip) · `redactions.jsonl` (append-only erasure trail:
ts/actor/path/count — FileStore-local, invisible to the kit). Foreign
files with ungrammatical names are skipped silently in listings; a valid
name with a broken or newer envelope raises. Naive timestamps in stored
data are refused, never coerced (`memory_format_unsupported`).

**One writer per root (NA ruling).** FileStore's `asyncio.Lock`
serializes mutations inside one process; across processes files cannot
arbitrate — which is why `supports_optimistic_concurrency` stays
`False` — and two writers on one root can interleave a read-modify-write
and lose an edit. Neosian documents the constraint instead of
engineering around it: no lock files, no `flock`, no leases. **A root is
owned by one writer at a time** — an agent's shell (`neosian memory`),
one MCP server process, or one embedding application — while any number
of readers may run beside it; the sidecar-before-document write order
bounds a concurrent reader to a stale read, never a reused version
number. Multi-writer needs route to `PostgresStore`, which arbitrates on
the version-row primary key, or to NM's state daemon, where one process
owns the files and every client speaks to it. Advisory locking would
half-promise arbitration at the price of a portability matrix (NFS,
Windows, containers) and a new failure mode — stale locks — on the
substrate whose entire value is that you can `cat` it (ledger #74).

**PostgresStore layout (N3).** The relational reference substrate for
standalone deployments, implementing both seams as one class
(`PostgresStore(dsn, *, schema="neosian", clock=None, min_size=4,
max_size=None, pool_timeout=30.0)` — the ctor is pure validation,
pool-sizing kwargs included: they default to psycopg's own values, are
validated at construction, and reach the driver only when the pool opens
lazily; `aclose()`/`async with` release it).
Tables in one dedicated schema (`memories` + `memory_versions` +
`memory_redactions` erasure trail; `conversations` + `turns` +
`projections`), every row carrying its ownership key (scope /
conversation_id) — RLS-friendly by construction. Discipline (ledger #34):
the pool runs `autocommit=True` and every mutation is **one
data-modifying-CTE statement** — gate CTEs suppress the writes when a
precondition fails and the top-level SELECT returns written row plus
pre-state diagnostics, so the store never issues BEGIN/COMMIT and one
round trip decides mutate-or-raise. Version and turn numbers race on
their primary keys and retry with a fresh snapshot;
`supports_optimistic_concurrency = True` because a losing
`expected_version` writer aborts on the version-row PK and re-reads.
Timestamps come from the injected Clock, never SQL `now()` (ledger #35);
`list_documents` orders `COLLATE "C"` (ledger #37); unknown substrate
keys ride an `extra` jsonb column (C6). The dormant FTS escape hatch is a
generated `tsvector` column + GIN index — populated, queryable by raw
SQL, no API surface. **Activation criteria (NG):** the hatch stays
dormant until recall demonstrably needs *content* search — a scope where
the budgeted index plus `view` paging (name and directory navigation)
misses facts the store holds, shown by harness scenarios at scale, never
asserted from intuition — and a real deployment asks for it. Activation
means new ABC surface (a recall/search method every host must
implement), so it is an ECOSYSTEM §12 session-pair, never a quiet method
on the reference store; until then the column remains an operator's raw-
SQL escape hatch. Substrate exception (ledger #38): Postgres
text/jsonb reject U+0000, so NUL-bearing content raises where FileStore
round-trips it. The DDL ships as `assets/sql/postgres.sql` (idempotent,
`{{schema}}`-rendered), applied only by explicit act —
`await store.apply_schema()` or `python -m neosian.schemas postgres` —
never by a store method (C1); alembic was rejected: hosts run their own
migration branch (ECOSYSTEM §10), and re-applying idempotent SQL is the
whole standalone story. That story is **additive-only** — `IF NOT
EXISTS` guards, no schema-version table: re-application converges, new
columns and tables arrive by appending to the asset, but a rename or
drop is outside the reference implementation's promise (stated here
before 1.0 turns silence into commitment; hosts needing real migrations
run their own branch). The `postgres` extra carries psycopg; the core
import stays driver-free (pinned by a subprocess test).

**The tool layer (N1 slice B; C7 made concrete).** `Mount(scope,
mount_path, read_only, description, edit_only)` + `MemoryConfig(store,
mounts)` in `memory/mounts.py`; ≥ 1 mount, unique mount paths — "memory
needs an explicit scope" is structural; `read_only` and `edit_only` are
mutually exclusive (a plain `ValueError`, the MemoryConfig precedent). One `memory` function tool (ledger #19),
`create_memory_tool(config, *, actor=None)`: Anthropic's command
vocabulary (`view/create/str_replace/insert/delete/rename`) over one
virtual path space — leading `/` optional, first segment selects the
mount, the rest is the store's document path. `view /` returns the same
rendering as `generate_memory_index` (one renderer, no drift with the
injected section); `view` of a document shows line-numbered content, of a
directory a prefix listing (always queried with a trailing `/` — `prefix`
is a plain string match). `create` is create-or-overwrite like
`store.write`, with a `system_reminder` naming the overwritten version
(ledger #20). `str_replace` requires exactly one occurrence (0 and N are
corrective failures listing match lines); `insert` splices at
`0 ≤ insert_line ≤ len(lines)`; both pass `expected_version` — free
lost-update detection. `rename` cross-mount is composed read + write +
delete and is **not atomic** (C1 forbids a cross-scope transaction).
Write enforcement lives here, never the store — two predicates:
`writable()` is the library's only `MemoryReadOnlyMountError` raise site,
`structural()` its only `MemoryEditOnlyMountError` one (see the
edit-only paragraph below). Every
`MemoryStoreError` is caught in the tool and returned as
`ToolResult.fail("[<code>] <message>")` plus a per-code hint — the model
self-corrects; nothing raises through the tool loop. The `command`
`Literal` compiles to a JSON Schema enum but is steering, not a runtime
guarantee (constrained decoding only under `strict=True`, Anthropic
only), so the dispatcher guards it: an unknown command string fails
correctively, naming the six valid commands — never a misleading
missing-parameter error from a fallthrough branch. The index +
`memory_system_section` (prompt pack rendered with `{{index}}`) are
async, caller-injected once per conversation — the CLI in N1,
`Conversation` in N2; `actor` is None until N2 threads `conversation_id`.
`AgentConfig.memory` auto-registers the tool (like todo/playbook/
blackboard), which makes a memory-enabled agent tool-enabled — structured
output is unavailable, as with any tool. The prompt pack ships in
`assets/prompts/memory.yaml` (§7); the tool description stays under the
1024-char OpenAI-compatible cap. **Native transport (N4).**
`AgentConfig.native_memory` / `create_memory_tool(native=True)` mark the
definition with Anthropic's `memory_20250818`: the wire description is
dropped (the native declaration has no field for one) while the injected
`memory_system_section` stays verbatim — the index and mount routing are
data the model cannot have (ledger #43). Local execution, mounts,
read-only enforcement and corrective failures are byte-identical either
way; the flag is inert off-Anthropic and never raises (ledger #42),
warning instead when the model is non-Anthropic or no mount sits at the
`memories` root §9.5.13 chose. The reference tool's argument vocabulary
is accepted first-class alongside the schema names — `file_text` as
`create`'s text (the trained emission; `content` wins when both arrive)
and `view`'s optional `view_range` — so native transport never hits an
unexpected-keyword failure.

**Edit-only mounts (NP slice B; ledger #103).** The document-set write
policy screened at NV and adopted at NP's opening discussion: an
edit-only mount (`edit_only=True`, argv token `eo`) fixes the *set* of
documents, never their contents — pre-created layouts the agent may
edit but not extend. `structural(mount)` raises
`memory_edit_only_mount` for anything that would change the set:
`create` of a path with no live document (an overwrite is an edit and
stays legal — the existence read `create` already does decides),
`delete`, and `rename` touching the mount on either side;
`str_replace`/`insert`/overwrite-`create` pass `writable()` alone.
Enforcement is per-command in `commands.py`, so all transports and
every internal dispatch consumer inherit it: `revert_memory` restoring
prior content works (its `create` lands on a live document), while
undoing a pre-`eo` `created` row refuses — the escape is re-mounting
without `eo`. Redaction on an edit-only mount is **allowed**: clearing
content is a content act, the document survives in listings. The index
header carries an `(edit-only)` marker beside `(read-only)`; §15/§16
state the reflection and maintenance treatment. `Mount` is tool-layer
policy, not an ECOSYSTEM seam — no session-pair.

**Write receipts & undo (NP).** Every successful mutating command
attaches a `MemoryWriteReceipt(command, mount_path, path, version,
previous_path)` to its `ToolResult` — built in `commands.py`, the one
place that holds mount, virtual path and store-assigned version together
(the delete receipt's version comes from `versions(limit=1)`; the ABC's
`delete` returns `bool`). The receipt is in-process only: `to_json()`
ignores it, so the wire envelope every transport prints is byte-identical
(#77), and its consumers are the `memory_write` event (§6),
reflection/maintenance result rows, and `revert_memory(config, path,
version=, actor=) -> ToolResult[str]` — the undo. `version` names the row
to *undo* and must be the newest (`memory_conflict`, reason
`revert_stale`); one rule covers every case: no live document before row
N → delete, otherwise row N-1's content comes back — executed through the
dispatcher (read-only enforcement, corrective mapping and the audit row
for free), appending a new version row, never rewriting history. Redacted
history refuses (C3's skeleton is deliberately not restorable). Undoing a
rename is two reverts — per-document by design; C1 forbids the cross-scope
transaction. `revert_memory` is host/operator surface: never a seventh
dispatch command, never registered on an agent, never MCP; its shell
face is the `neosian memory revert` operator verb (§14.2, NP slice B).

**The index at scale (NG).** The one renderer takes a
`budget_chars` keyword (default `INDEX_BUDGET_CHARS = 8192`, ~2k tokens
under §6's char heuristic; module-local, no `MemoryConfig` field — the
#92 no-unneeded-config discipline) and applies §9.6's log-projection to
the index itself. Under budget the rendering is byte-identical to the
one-line-per-document form. Over it, three tiers: hot — documents keep
their lines, promoted newest-`updated_at`-first (ties by path; the
promotion stops at the first document that no longer fits — a recency
cutoff, never a knapsack); warm — the rest fold into per-directory count
lines (`- /notes/projects/ (12 documents)`, loose documents as
`- /notes/ (7 more)`); cold — a mount that cannot hold even its folds
collapses to its total. The selection is a pure deterministic function
of the listings — no model call, no state — and length accounting runs
over the same line helpers the renderer uses, so a promotion that
*shrinks* the render (a fold line can be longer than the document line
it covers) still lands. A folded region is re-hydrated with `view` of
the directory: paging, never deletion — the prompt pack says so beside
the index. Honest bound: the floor is returned even when it exceeds the
budget (pathologically many mounts or top-level directories); the
500-document test pins the realistic case. `view /` serves the same
budgeted rendering — one renderer, no drift. `neosian.mcp` serves the same memory over
the Model Context Protocol on stdio: `python -m neosian.mcp --root PATH
--scope user:me` (or the pass-through `neosian mcp`), or
`await create_memory_server(config, *, actor="mcp")` for hosts that embed.
The MCP tool is *the* tool — `create_memory_tool`'s `ToolDefinition` is
served verbatim through the SDK's low-level `Server` (explicit
`input_schema`, never type-hint re-derivation), so the function tool, the
native `memory_20250818` declaration and the MCP `tools/list` entry are
three transports over one definition. NA's memory CLI (§14.2) makes it
four: `build_memory_tool` owns the one wire definition, each transport
supplies only its execution, and the shell rides the same ladder. Execution is shared too: the
command ladder lives in `memory/dispatch.py`, so the unknown-command
guard, the per-command argument checks, the `file_text` alias and the
`MemoryStoreError → "[code] message"` + hint mapping run once for all
three (ledger #50). `ToolResult` maps to MCP's native shape rather than
the function tool's JSON envelope — data or error as a text block,
`system_reminder` as a second block, `is_error` carrying the failure —
which is exactly what MCP's in-band tool errors are for (ledger #52). The
server's `instructions` are `memory_system_section(config)`, rendered at
construction and again per connection in the SDK lifespan: the
per-session analogue of the frozen-index-per-conversation rule (ledger
#51). Configuration is flags plus one env var — mounts and the FileStore
root in argv, Postgres only through `NEOSIAN_POSTGRES_DSN` (#53, renamed
at NA #76), never a
DSN on a command line; the `--scope` sugar mirrors
`memory_scope=` (one rw mount at `memories`); mount descriptions are
factory-only prose. The entry point owns the store's lifetime and closes
the pool on exit — the factory and the per-connection lifespan never do.
The `mcp` extra carries the SDK (`mcp>=2,<3`); `import neosian` and
`import neosian.mcp` stay SDK-free (pinned by subprocess tests).
Streamable HTTP is deferred — an embedding host mounts the factory's
server on its own transport.

## §9 Conversation & compaction **(N2)**

Decided in the N2 opening design discussion (2026-08-19); slice A implements
§9.1–§9.5 and §9.7–§9.9, slice B implements §9.6, slice C migrates the CLI.

**§9.1 The layer.** `Conversation` is the opt-in stateful shell around the
stateless core: it owns history, memory wiring, and compaction; `Agent` is
never mutated, never subclassed, never smuggles state. The two-key model:
`conversation_id` keys history (per thread); memory keys off mounts. Out of
scope — a commitment, not a suggestion: branching/forking, history editing
(ruled out in VISION), transport, auth. `send()` has no `response_format`
parameter: a memory-enabled agent is tool-enabled, so structured output is
unavailable by construction, as with any tool.

**§9.2 The `ConversationStore` contract — constraints CS1–CS7** (numbered
independently of §8's C1–C7):

- **CS1 — async signatures, zero I/O ownership.** §8 C1 verbatim: no
  `__init__` in the ABC, no connect/close/migrate/DDL, never begins/commits/
  rolls back a transaction; safe inside a caller-owned transaction; no
  durability assumed on return.
- **CS2 — read-your-writes within a task.** `append_turn` then `read_turns`
  in the same task sees the appended turn — resume correctness demands it.
- **CS3 — store-assigned turn numbers: per-conversation, monotonic, gapless,
  from 1, never reused.** Callers never propose a number. `recall_turn`
  addresses turns by number and projection coverage is arithmetic over a
  dense range; Postgres implements `COALESCE(MAX(turn),0)+1` under
  `UNIQUE(conversation_id, turn)` with retry.
- **CS4 — UTC-aware datetimes, injectable `Clock`.** `created_at` is the
  store's, never the caller's; naive is a contract violation the kit asserts
  against.
- **CS5 — verbatim messages.** What is appended is what is read back — role,
  content (string or blocks), `reasoning`, `tool_calls`, `tool_call_id`. A
  store may not summarize, reorder, drop, or re-key. This is why the message
  codec is public (§9.9).
- **CS6 — `CONVERSATION_FORMAT_VERSION = 1`** on every stored row; refuse to
  read newer (`agent_conversation_format_unsupported`). Unlike §8 C6 there is
  no unknown-key preservation: turn rows are library-owned, never
  hand-edited; unknown keys are ignored on read.
- **CS7 — no policy, no interpretation.** The store never validates message
  shape, never checks that a projected turn exists, never trims, compacts, or
  invents ids. Compaction, banding, recall, index injection, and actor
  binding are Conversation-layer concerns.

```python
class ConversationStore(ABC):
    async def append_turn(conversation_id, messages: Sequence[Message]) -> ConversationTurn
    async def read_turns(conversation_id, *, after=0, limit=None) -> tuple[ConversationTurn, ...]
    async def last_turn_number(conversation_id) -> int            # 0 for unknown
    async def append_projections(conversation_id, entries) -> None
    async def read_projections(conversation_id, *, after=0, limit=None) -> tuple[ConversationProjection, ...]
```

`read_turns` returns turns with number > `after`, ascending; `limit` takes
the oldest N after the cursor (`limit=0` → `()`); negative `after`/`limit`
and empty `messages` are programmer errors (`ValueError`).
`read_turns(after=n-1, limit=1)` is the recall lookup — no sixth method.
**Deliberately absent:** `list_conversations` (hosts list from their own
tables), delete/redact (no roadmap requirement; a retrofit is an additive
method landing at a minor bump per ECOSYSTEM §11 — §9.10), per-turn
usage/model/cost columns (§9.3), and any
`supports_*` ClassVar (nothing varies by substrate; concurrency is answered
normatively by CS3).

**§9.3 Value types** (fields are contract, pinned by the kit).
`ConversationTurn(conversation_id, turn, messages: tuple[Message, ...],
created_at)`. `ConversationProjection(turn, kind, text, span=1)` — `turn` is
the last turn the entry covers, `span` the count of consecutive turns ending
there (`1 ≤ span ≤ turn`); `kind: ProjectionKind =
Literal["log", "digest", "epoch"]`; no timestamp — a projection is derived
data whose provenance is the turn range it names. Ruling: turn rows carry
**no usage/model/cost** — hooks (`on_turn`/`on_llm_call`) are the metering
seam; a storage seam demanding token columns taxes every host implementer
for data they already have.

**§9.4 `conversation_id` grammar.** `\A[A-Za-z0-9_.-]{1,128}\Z` — one flat
segment, no `/` or `:`, bare `.`/`..` rejected, ≤ 128 chars; validated on
every store method (`agent_conversation_id_invalid`), **never interpreted**
(the ECOSYSTEM §2 discipline applied to the second key). A host with
structure encodes it flat. Case-insensitive filesystems are a legal
substrate, so ids differing only in case are not guaranteed distinct.

**§9.5 Conversation semantics** — normative rulings, each pinned by a test:

1. **Append-only.** One `send()` appends exactly one turn:
   `(user_message, *response.turn_messages)` in provider order. History is
   the concatenation of every turn's messages in turn order. System messages
   are configuration, never stored.
2. `turn.messages[0]` is always the USER message that opened the turn — the
   invariant the slice-B projector builds on.
3. **The persistence seam is `on_turn`** — the only point where blocking and
   streaming agree (`run(stream=True)` never returns an `AgentResponse`;
   `DoneEvent` carries usage, not messages).
4. **The hook captures; `send()` writes.** `HookRunner` swallows hook
   exceptions unless `strict` — a store write inside the hook would lose
   turns silently, and forcing `strict=True` would hijack the user's own
   hooks. Conversation's hook only stashes the response; the write happens in
   `send()`'s control flow where errors propagate (ledger #25).
5. **Blocked turns persist nothing** — `turn_messages` is empty on blocked
   (§3), so the rule is mechanical: persist iff non-empty. `send()` still
   returns the blocked response; history is unchanged. A host wanting a
   blocked-input audit trail uses its own `on_turn`, which still fires.
6. **A raising `send()` persists nothing** and leaves history untouched; the
   user message is not retained — retry is calling `send()` again. No
   half-turns, ever.
7. **Streamed turns persist at the terminal event.** `emit_turn` fires
   before the terminal yield (register #6), so Conversation persists the
   captured turn *before relaying* the terminal event — a consumer that saw
   `done`/`blocked` holds a persisted turn unconditionally, even if it stops
   iterating there. A store failure surfaces in place of the terminal
   event. Abandoning a stream before the terminal persists nothing — the
   turn never completed.
8. **One in-flight `send()` per Conversation** — an `asyncio.Lock`, held
   across the blocking call and for the streaming generator's lifetime.
9. **Lazy start.** Construction is sync and does no I/O; the first `send()`
   (or explicit `await start()`, idempotent) loads turns and freezes the
   memory index. **Resume = construct with the same `conversation_id`.**
10. **Frozen index per conversation** — `memory_system_section` rendered
    exactly once per Conversation instance, appended to the system prompt
    (`f"{system_prompt}\n\n{section}"`, the N1 playground assembly). The
    slice-B compaction boundary is the one legitimate refresh point.
11. **The caller's `Agent`/`AgentConfig` is never mutated.** Conversation
    derives its own config (`dataclasses.replace`): section appended, memory
    tool rebuilt with `actor=conversation_id`, `memory=None` on the derived
    config (or the agent would register a second, unbound tool), hooks
    composed — capture first, the user's `on_turn` after, the other three and
    `strict` passed through. It accepts `Agent | AgentConfig`; `Agent`
    exposes read-only `config`/`max_tool_iterations` for exactly this (§3).
12. **`actor = conversation_id`** on every memory mutation — the audit
    trail §8 promised.
13. **Memory arguments are exclusive.** At most one of `memory=` /
    `mounts=` / `memory_scope=` (else `ConfigurationError`); `mounts`/
    `memory_scope` require the store to also implement `MemoryStore`; none
    given falls back to the config's own `memory` (re-wired with the actor);
    otherwise no memory and no index section. `memory_scope="user:123"`
    builds a one-mount list at mount path `memories` — the path Anthropic's
    native `memory_20250818` roots at, so N4's native wiring stays a pure
    transport swap.
14. **One session per Conversation (slice C).** The layer opens an internal
    `AgentSession` on first use and reuses it for every send *and* for
    compaction's distillation calls — one cached client per provider
    instead of a fresh one per attempt, and sticky fallback state across
    sends (§3). The boundary rebuild *rebinds* that session to the newly
    derived agent, so paging never costs a reconnect. `aclose()` releases
    the pool and is idempotent; `async with conversation:` is the sugar
    (entering does no I/O — lazy start is ruling 9). Not closing is safe:
    the pool lives as long as the process, exactly as an unclosed
    `AgentSession` does, and a send after `aclose()` opens a fresh pool.
    `aclose()` never *waits* on the send lock (an abandoned stream holds
    it until collected) — finish or abandon a stream before closing.
    Since NR, a memory-bearing close first runs the §15 reflection pass
    and returns its `ReflectionResult | None` (a lock still held skips
    reflection with a warning; the pool always closes).

**§9.6 Compaction v1 — log-projection (spec; implemented in slice B).** The
context window renders a *view* of the append-only history: recent turns
verbatim (hot), aged turns as typed one-line log entries (warm), oldest
spans folded into epoch summaries (cold). The view is a pure function over
`(turns, projections)`. Entries are computed per turn, once, and
checkpointed via `append_projections` — never re-summarize the transcript;
the checkpoint head is derived (`max(turn)` over projections), never stored
separately. Deterministic projection first: tool rounds have known shape
(`TOOL <name>(<args digest>) → <result head/tail>`) — free, no model call.
Model distillation only for long prose, batched at compaction boundaries via
structured output (k turns in → k log lines out, one call), `kind="digest"`.
Selection: for each turn the widest `span` covering it wins, ties to the
last appended — a fold supersedes without mutation; entries are never
deleted. The built-in `recall_turn(turn)` tool re-hydrates the verbatim turn
via `read_turns(after=turn-1, limit=1)` — compaction is paging, not
deletion. User turns are compacted least aggressively; stated constraints
and decisions survive verbatim. Trigger: `ContextPolicy.estimate_tokens`
over the rendered view against `Model.context_window` at a configurable
high-water fraction (the deliberate underestimate stays the safe direction;
the reactive 400 wrap stays the backstop). The memory index refreshes at the
compaction boundary — the free cache moment. Distillation calls are ordinary
LLM calls reporting through `cost_micro_usd`/`usage_by_model` — compaction
spend is visible, never hidden. Role labels stay full words (`USER`, `TOOL`,
`AGENT`). Surface: `CompactionConfig(enabled, model=None /* the agent's */,
hot_turns, trigger_fraction, digest_chars, epoch_turns, recall_tool=True)`,
arriving as `Conversation(..., compaction=…)` in slice B.

*Implementation notes (slice B, 2026-08-20).* Default-on with lazy
`recall_turn` registration (ledger #28); a public `Conversation.compact()`
runs one boundary on demand — skips the high-water check and `enabled`,
respects `hot_turns`, returns `CompactionResult(entries, usage, model)`.
The view is keyed on coverage alone (ledger #27); the trigger runs in
`send()` before the agent call, at most one boundary per send. Epoch
folds are model-written summaries over fixed-aligned `epoch_turns` blocks
wholly past the cutoff (a second structured-output call; a failed fold is
skipped and retried at the next boundary — never a deterministic
substitute). Digest failure degrades the affected turns to deterministic
`kind="log"` lines; a store failure at the checkpoint fails the send with
nothing persisted. Honesty bounds: "constraints survive verbatim" holds
for USER text up to `4 × digest_chars` with a recall pointer beyond
(ledger #31); a turn with many tool rounds yields one *long* line —
per-segment caps, no whole-line cap (epoch folding is the length
discipline); tie-to-last-appended is realized as later-in-`(turn, span,
insertion)` order, identical whenever the tie is real; overlapping
hand-written projections render total and deterministic, not reconciled.
Since slice C the `acquire` callable is a **lease** — the caller of
`run_boundary` owns the client's lifetime (ledger #33); Conversation hands
its session's cache, so distillation rides the send's pooled clients.

*Server-side compaction stays out (N4).* Anthropic's compact beta is an
**agent-level** opt-in (`AgentConfig.server_compaction`, §2), never a
Conversation mode: the view's log-projection replaces aged turns with log
lines, dropping any server compaction blocks they carried — the server
would re-compact (and re-bill) the same span on every send. Conversation
warns at construction when the flag is on (ledger #49); log-projection
remains the foundation, per the ROADMAP ruling ("an opt-in optimization
where available, never the foundation").

**§9.7 The shipped conformance kit** —
`neosian.conversation.testing::ConversationStoreContract`, the §8 mechanism
applied to the second seam: subclass, provide a `store` fixture, inherit
~26 tests (numbering, verbatim round-trip, UTC-awareness, ordering/cursor
semantics, isolation, id rejection, projection semantics, format refusal).
Substrate-planting tests ride one overridable `plant_raw_turn` hook,
skipping where unimplemented.

**§9.8 FileStore turn layout.** `root/conversations/<conversation_id>/
turns.jsonl` + `projections.jsonl`, sibling of the percent-encoded scope
directories — every scope directory component carries `%3A`, so
`conversations/` can never collide with a scope. One compact
`ensure_ascii=True` JSON object per line (no message content can break
framing), `neosian_format` on every row, ISO-Z timestamps (naive refused,
never coerced). Pure appends — no rewrite path in v1; mutations share the
memory side's in-process `asyncio.Lock`, so a turn append and a memory
write never interleave. `last_turn_number` reads the last line — the
numbering's source of truth is the file, never a line count. A malformed or
newer row raises, never skips (skipping would silently drop a turn and
corrupt the numbering). A missing file is an empty history, never an error.
The Postgres sibling (N3, §8 layout paragraph): CS3 rides
`COALESCE(MAX(turn),0)+1` under `UNIQUE(conversation_id, turn)` with
retry, messages land in a jsonb column via the public codec, and an
identity column on `projections` realizes the (turn, span, insertion)
read order.

**§9.9 Public surface & the codec ruling.** `message_to_json` /
`message_from_json` become public API: a host implementing
`ConversationStore` must encode messages with the same codec the library
reads back — leaving it private forces every host to hand-roll half a codec,
the exact drift CS5 forbids (ledger #22). Root `__all__` gains the
conversation block + the two codec names; `neosian.conversation` (+
`.testing`) mirrors `neosian.memory` (lazy, re-export only). `FileStore` is
re-exported from both facades — it implements both seams; `ProjectionKind`
and `ConversationId` stay facade-only, like `MemoryAction`.

**§9.10 The ECOSYSTEM amendment (executed 2026-08-21, N4 docs/1.0).** The
amendment deferred here since N2 landed at v0.70.0 (first cut as v1.0.0,
withdrawn the same day — 1.0 postponed, ledger #73): ECOSYSTEM §10 names
`ConversationStore` + `ConversationStoreContract` (the seam is frozen for
hosts; the ABC docstring's contract-of-record caveat is retracted); §6
**blesses the shipped `agent_conversation_*` codes and deliberately declines
a `conversation_` prefix** (user ruling, 2026-08-20 audit — the codes
shipped at v0.60.0 and the append-only rule forbids renaming them, ledger
#24); §11 states the SemVer guarantee from v1.0.0; the §12 changelog row
sweeps the two host-visible deltas the audit flagged as unrecorded — the
public message codec (`message_to_json`/`message_from_json`, ledger #22)
and `CompactionBlock` joining the content union (ledger #46). The kit's
side completed the same day: kit ledger #206 at its 73dcd39 (the §15 row
+ §5.7 seam-list clause); the changelog cell is filled (ledger #71).

## §10 Test harness & gates

Tiers: `unit` (fakes only — FakeProvider over ad-hoc `AsyncMock`; the default
gate, fully keyless) and `external_<provider>` (real API calls; renames the
old real-API "integration" tier to the ecosystem vocabulary). pytest:
`--strict-markers`; addopts exclude every `external_*` by default (opt-in by
marker, not path); auto-mark by path in the root conftest; skips are per-test
helpers, **never module-level** (module-level skip makes pytest exit 5 in an
empty selection); the key-presence check tests **falsiness**, not `None` — an
absent CI secret arrives as the empty string. Credential injection is
value-blind via `scripts/external_env.py` (per-suite key allowlist; prints key
names only).

File-size gate: warn 300 / fail 500 lines (`scripts/check_file_size.py`, kit
idiom: allowlist entries carry a mandatory `# reason`; a stale entry fails).
`agent/base.py` entered the allowlist at NH ("split lands in N0"); the N0
session-twin collapse + module split took it back out.

## §11 Tooling & release

**Makefile** (created in NH): `.DEFAULT_GOAL := help`, self-documenting `## `
comments, comment bands, one `.PHONY` line.

| Target | Contract |
|---|---|
| `help` | list targets (default goal) |
| `install` | `uv sync --locked --all-groups` |
| `lint` | `ruff check .` + `black --check .` + `lint-imports` |
| `format` | `black .` + `ruff check --fix .` |
| `typecheck` | `mypy --strict neosian tests` |
| `test` | unit tier — the default gate, zero keys |
| `test-external` | `provider=<openai\|anthropic\|cerebras>` required-arg guard; `file=…` routes through `scripts/external_env.py` |
| `test-postgres` | the `external_postgres` suite; needs `NEOSIAN_TEST_POSTGRES_DSN`, self-skips per test when unset — a DSN is not an API key, so it never routes through the value-blind injector |
| `size` | the 300/500 file-size gate |
| `release` | refuses a dirty tree; requires `v=X.Y.Z` matching pyproject; cuts annotated `v$(v)` |
| `phase-tag` | refuses a dirty tree; cuts annotated `<id>-done` |

**CI** (`.github/workflows/ci.yml`, reshaped in NH): triggers push-master /
PR / dispatch — no schedule since ledger #89 (real-API runs are deliberate,
dispatched acts, never a standing cost); **event-keyed concurrency group**
(a master push must not cancel a dispatched run); runners pinned
`ubuntu-24.04`; `setup-uv` with cache. Jobs: `lint` (lint + typecheck +
size) · `test` (matrix **3.12 / 3.13 / 3.14**, fail-fast false, **no
secrets** — that absence is the keyless-boot assertion) ·
`external-<provider>` × 3 (dispatch only; secret as env; suites self-skip
on empty string).

**Versioning** (flipped in NH): the number lives once in
`pyproject [project].version`; `__version__` derives via
`importlib.metadata.version("neosian")`; `tests/unit/test_version.py` pins the
derivation. `[tool.hatch.version]` is deleted. Progress axis = annotated
`<id>-done` phase tags; **release tags `v<X.Y.Z>`** are what hosts vendor
(ECOSYSTEM §11). Bump on public-surface change at phase close, not per commit.

## §12 Deviations & decisions ledger

Every deliberate departure from a prior behavior, a reference pattern, or an
obvious alternative. New entries land in the same session that decides them —
never a silent divergence. Numbering is monotonic, never reused.

| # | Alternative / prior behavior | Neosian decision | Why |
|---|---|---|---|
| 1 | Float USD `Usage.cost()` (v0.51) | **Integer micro-USD canonical**; `cost_micro_usd() -> int\|None`, ceiling division; float dropped | Money-as-floats undercounts and drifts; the ecosystem meters in µ$; zero production call sites made the break free |
| 2 | Anthropic-flavored token names (`cache_creation_input_tokens`, `cache_read_input_tokens`) | **The four ecosystem classes**: `input/output/cache_read/cache_write_tokens` | One vocabulary across library and hosts; provider flavor is an internal mapping problem |
| 3 | Store as the integration point (host uses neosian's PostgresStore) | **The ABC is the contract**; FileStore/PostgresStore are reference impls; hosts may implement their own | A store that owns connections/commits/DDL cannot embed in a host's transaction discipline |
| 4 | Sync wrapper for notebooks/servers | **Async-only stays** (VISION ruling, reaffirmed) | `asyncio.run()` in library code deadlocks exactly where it is most tempting |
| 5 | Match the kit's Python 3.14 pin | **Floor stays ≥3.12; CI matrix 3.12–3.14** | An app picks its runtime; a library is picked by its hosts' runtimes — the matrix proves 3.14 |
| 6 | `.claude/` gitignored | **`.claude/commands/` tracked**; only `settings.local.json` ignored | Procedures that don't travel with the repo aren't procedures |
| 7 | `v:X.Y.Z. <summary>` commit subjects | **`<PHASE-ID>: <what became true>`** + annotated tags; releases are `v<X.Y.Z>` tags | Progress and releases are different axes; subjects should carry outcomes, not version bookkeeping |
| 8 | ruff UP042 (StrEnum) | **`(str, Enum)` kept, UP042 ignored** | `StrEnum` changes str() semantics the wire format relies on (pre-existing ruling, now recorded) |
| 9 | Scope hierarchy parsed by the store | **Scopes are opaque**; hierarchy = explicit mounts | Parsing semantics out of keys is inheritance logic no two hosts want the same way |
| 10 | Error codes renamable while pre-1.0 | **Codes append-only from NS** | Hosts key i18n and alerting on them the day they exist |
| 11 | No license file | **Apache-2.0** | Open-core: permissive lib for adoption (+ patent grant); product value lives in hosts; sole copyright holder keeps relicensing freedom (add DCO/CLA if external PRs are ever accepted) |
| 12 | Raw SDK exceptions escaping / stringified into fallback errors | **`wrap_provider_error` at the client boundary**; provider, status, retryable preserved; `raise … from exc` | Structure survives to the host's logs and wire codes; `str(e)` destroyed both |
| 13 | A provider reported "Request too large" with HTTP 413 (Groq, removed at NW step 0 — #84) | **Context-overflow classifier keys on 400 only**, as specced; 413 widening deferred | Widening blind risks misclassifying non-context 413s; append the status when a real one is observed. Moot for the shipped providers since the exit; kept for the record |
| 14 | Hide fake models from user-facing errors | **`Model.FAKE*` appear in the INVALID_MODEL supported-models listing** | Fakes are real registry members (DESIGN §2); filtering would special-case the registry for cosmetics |
| 15 | Event name only in the SSE `event:` line (§6 leaves the payload unstated) | **`to_dict()` carries the `event` discriminator key**; payload TypedDicts are the schema-export source | Hosts codegen validators from `event_schemas()` — schema and payload must be the same shape, and a self-describing JSON object survives outside SSE framing |
| 16 | ContextPolicy opt-in, or exact per-provider tokenizers | **Default-on, char-heuristic, deliberately underestimating** (`AgentConfig.context_policy=ContextPolicy()`; `None` disables) | Makes `context_window` live for every user; underestimation means it only fires on clear overflow — false positives impossible in practice, the reactive 400 wrap stays the backstop |
| 17 | Caller-input errors wrapped in `ModelFailedError` at no-fallback branches | **`ContextWindowExceededError`/`UnsupportedContentError` re-raise as-is** (billed usage attached), like the fallback-gate already did | "Your prompt doesn't fit" is not a model failure; wrapping buried the structured window/estimate fields hosts key on |
| 18 | import-linter counts `TYPE_CHECKING` imports (its default; N1's memory ↛ provider-internals contract tripped on `exceptions →(TC) llm.base` and `types →(TC) llm.fake`) | **`exclude_type_checking_imports = true`** for all contracts | The §1 contracts police runtime coupling; type-only imports create none, and per-edge `ignore_imports` whack-a-mole would rot |
| 19 | Six function tools, one per command (the plain reading of ROADMAP/VISION "six-command tool set as plain function tools") | **One `memory` tool with a `command` enum**, flat all-optional schema, per-command checks in-tool with corrective failures | The name + shape frontier models are post-trained on (`memory_20250818`); one definition per request instead of six near-clones; the N4 native flag becomes a pure transport swap. The command vocabulary is preserved verbatim — only the packaging changed |
| 20 | `create` refuses an existing path (Anthropic's reference local memory tool opens `O_EXCL`) | **`create` = `store.write`** (create-or-overwrite, version bumps), with a `system_reminder` naming the overwritten version and pointing at `str_replace` | Refusal would fork the tool's semantics from §8's write ruling; the reminder teaches the same discipline without a second code path, and every overwrite stays recoverable through the version rows |
| 21 | Extend `MemoryStore` with turn methods (one store, one ABC) | **A separate `ConversationStore` ABC**; `FileStore` implements both | Different shapes (scope+path+versions vs conversation+turn+projections), different implementers (a host may keep turns in its own tables and memory in ours); growing a shipped ABC breaks every existing implementation and the conformance kit |
| 22 | JSON dicts at the turn-storage seam (dumb stores, codec stays private) | **Typed `tuple[Message, ...]` at the seam; `message_to_json`/`message_from_json` go public** | A dict seam pushes encoding into every host and guarantees divergence exactly on `tool_call_id`/content blocks/reasoning — the fields the CLI's lost-tool-history bug was made of |
| 23 | Projection surface deferred until compaction lands (slice B) | **`append_projections`/`read_projections` in the ABC from day one**, unused until slice B | An ABC gains methods only at the cost of every implementation; compaction's storage shape is already known — cheap now, painful to retrofit |
| 24 | A `conversation_` error-code family | **`agent_conversation_*`** under the existing family prefix | ECOSYSTEM §6's prefix set is closed and frozen; opening it is a two-repo move this session cannot make, codes are append-only so the naming is permanent, and hosts key on full code strings — the taxonomy is cosmetic |
| 25 | `on_turn` performs the store write (the obvious reading) | **The hook captures; `send()` writes** | Hook exceptions are swallowed unless `strict` — persistence inside a hook loses turns silently, and forcing `strict=True` would change the semantics of the user's own hooks |
| 26 | Strict indirect-import checking for the conversation layering contract (the memory contract's setting) | **`allow_indirect_imports = true`** for conversation ↛ providers, plus a second contract: the storage-seam modules never import the agent | `Conversation` legitimately drives an `Agent`, which owns the router; policing only the direct edge is the honest version of the §1 promise, and the seam-modules contract catches the coupling that actually matters |
| 27 | View render keyed on `(turns, projections, hot_turns)` — compacted only when covered **and** outside the hot band | **Coverage alone decides the render**; `hot_turns` is a boundary-time writer invariant only | §9.6 defines the view as a pure function over `(turns, projections)`; a resumed conversation renders identically under config drift, and "computed once per turn" stays literally true |
| 28 | Compaction opt-in (`compaction=None` → off) | **Default-on** (`CompactionConfig()`), with `recall_turn` registered lazily once projections exist; `enabled=False` gates only the automatic trigger — `Conversation.compact()` (the manual `/compact` idiom) always runs | Ledger #16's philosophy: `Conversation` is the one place history grows unboundedly, so opt-in ships the failure mode enabled; lazy registration keeps a never-compacted conversation byte-identical to v0.60.0 |
| 29 | `AgentResponse.usage` is the agent run's usage only | **`send()` folds distillation/epoch usage into the returned `AgentResponse` and the streamed `DoneEvent`/`BlockedEvent`** via `dataclasses.replace`, merging a `usage_by_model` entry for the distillation model; `compact()` returns spend on `CompactionResult` | §9.6 requires compaction spend visible through `cost_micro_usd`/`usage_by_model`; the send's return value is the only object the caller holds, and the per-model split keeps the two spends separable |
| 30 | `context_policy=None` disables compaction's trigger too | **Compaction falls back to a default `ContextPolicy()`** for its high-water check | Ledger #16's `None` disables the *pre-call raise*, not paging; a Conversation that silently stops compacting because the proactive guard was turned off is the footgun ContextPolicy exists to remove |
| 31 | "Stated constraints survive verbatim" as unbounded verbatim USER text | **USER text is never distilled and is verbatim up to `4 × digest_chars`, then head-clipped with an inline `recall_turn(n)` pointer** | An unbounded USER line makes the view un-shrinkable — one pasted document defeats every boundary; the pointer keeps the full text one tool call away, which is what "paging, not deletion" promises |
| 32 | Distillation through a second minimal `Agent` | **A raw client call through an injected `acquire(provider)` callable** (`Agent._create_client`, honoring `client_factory`), `cache_conversation=False`, closed after the call | A derived-config agent re-fires the capture hook and clobbers the captured turn; a base-config agent fires the user's `on_turn` twice per send; either way a tool-bearing agent rejects `response_format` (`_validate_run`). The callable also keeps all four compaction modules agent-free, so they join the ledger #26 storage-seam contract |
| 33 | Distillation closes the client it acquired (#32: "closed after the call") | **`acquire` is a lease: the caller of `run_boundary` owns the client's lifetime**; Conversation hands its internal session's cache, and `aclose()` is where clients close | Closing a *cached* client leaves a dead handle in the session pool — every later send would ride a closed httpx client; the reuse §9.5.14 promises is impossible while the callee owns the close |
| 34 | C1/CS1 "owns no connection" read as binding the reference impls too | **`PostgresStore(dsn)` owns a lazily-opened `autocommit=True` pool**; every mutation is one data-modifying-CTE statement, so the store never issues BEGIN/COMMIT/ROLLBACK and assumes no durability beyond a statement | C1 constrains the *ABC* so hosts can embed their own stores in their own transactions (ledger #3); the standalone reference impl must own connections to exist, and single-statement atomicity honors the constraint's spirit exactly |
| 35 | `created_at` from SQL `now()` (server time) | **Timestamps come from the injected `Clock`, passed as parameters** | C4/CS4 demand the injectable clock (the kits' ManualClock proves it); consequence stated honestly: workers with skewed wall clocks can write out-of-order `created_at` while turn numbers stay gapless — numbering is the database's, time is the application's |
| 36 | The kits' `plant_raw_*(line: str)` hooks assume a JSONL substrate | **Object-shaped lines map field→column; an unparseable line is planted as `neosian_format = 0`** | The relational equivalent of a row with no readable format marker — both planting tests stay meaningful (raise, never skip) without pretending a jsonb column can hold arbitrary bytes |
| 37 | `ORDER BY path` under the server's default collation | **`ORDER BY path COLLATE "C"`** | "Sorted by path" means Python codepoint order in FileStore; without the pin the same store sorts differently per server locale |
| 38 | §8's byte-exact content round-trip, universally | **Postgres text/jsonb reject U+0000** — NUL-bearing content raises the driver error, documented in the class docstring | A substrate limitation, not a policy: the kit never plants NUL so conformance is unaffected, but the promise needed the recorded exception rather than a surprise traceback |
| 39 | Retry exhaustion re-badged as a fourth `MemoryConflictError.reason` | **After the attempt cap the driver exception propagates** | The `reason` set is documented and machine-checkable; widening it for an effectively-unreachable state (losses are bounded by writers in flight) taxes every host matching on it |
| 40 | Postgres CI mirrors the schedule-only provider jobs | **The `postgres` job runs on every push/PR** (service container, DSN via env) | It needs no secret and costs nothing, so gating the conformance suite to a weekly run would leave the phase's core unprotected; the keyless `test` job still receives nothing |
| 41 | `native_type` as a public `@Tool(...)` parameter — any tool declarable as any provider-native type | **Internal marker only**: `ToolDefinition.native_type` + `set_native_type()`, reachable solely through `create_memory_tool(native=True)` | `ToolDefinition` is not public, so the field costs no surface; a user-settable native type is an unreviewed feature whose failure mode is a provider 400 on a tool that silently stopped carrying its schema |
| 42 | `native_memory=True` validates like `reasoning_effort` (raise on no-memory / non-Anthropic model) | **Inert, with one warning per condition** (non-Anthropic main model; no `memories` mount) | `derive_config` deliberately ships `memory=None` with the tool in `tools`, so a `__post_init__` raise makes Conversation + native memory structurally impossible; and under fallback the answering model may not be `config.model`. Unlike a dropped `reasoning_effort`, a dropped marker changes nothing observable — same schema, same execution |
| 43 | Drop the memory prompt pack under native mode — the trained behavior replaces it | **The wire description goes (the native declaration has no field for one); `memory_system_section` stays verbatim** | The index and mount routing are data the model cannot have; keeping the section is what makes the flag a *transport* swap, and lets the N4 eval harness compare transports rather than prompts |
| 44 | A `ModelSpec.supports_native_memory` capability field + a fallback gate | **No field, no gate** | The marker degrades by construction — every non-Anthropic converter reads only name/description/parameters and emits the function schema (pinned per client). A gate would guard a failure mode that does not exist, at the cost of a value on every registration |
| 45 | `server_compaction` as a client-constructor flag or a config object | **A per-call `bool` on the ABC**, the `cache_conversation` precedent; other clients `# noqa: ARG002` it | `AgentSession` caches one client per provider — a per-client flag makes cached clients silently config-dependent; a `ServerCompactionConfig` type is a three-place `__all__` diff every provider must honor for one provider's beta knobs (`instructions`/`trigger`/`pause_after_compaction` wait for demand) |
| 46 | A parallel `Message.provider_blocks` field for compaction blocks | **`CompactionBlock` joins the `ContentBlock` union** | The union already has a codec, a persistence path, and a capability gate — a parallel field rebuilds all three. Consequence stated honestly in `Message`'s docstring: assistant messages now legitimately carry block lists (text + compaction; media still raises) |
| 47 | `provider is ANTHROPIC` as the compaction support check | **`ModelSpec.supports_compaction_blocks`**, True on Opus 5 / Opus 4.6 / Sonnet 5, False on Haiku 4.5 | Haiku 4.5 is Anthropic *and* outside the compact beta's support set — the provider check ships a guaranteed 400. One field serves both the request pre-flight and the history-side fallback gate (contrast #44, where no failure mode existed) |
| 48 | A `compaction` AgentEvent so hosts see paging happen | **Invisible to the event vocabulary; spend folds into `Usage`** | ECOSYSTEM §5 is frozen (a new event is a two-repo move) and a compaction block is context bookkeeping, not user-visible content. The beta reports summarization tokens only under `usage.iterations` — the client folds compaction entries into the reported `Usage`, keeping ledger #29's money-visibility promise |
| 49 | Server compaction as a `CompactionConfig` mode Conversation can ride | **Agent-level only; Conversation warns when the flag is on under it** | Log-projection drops replaced turns' messages at the warm boundary — server compaction blocks vanish with them and the server re-compacts (and re-bills) the same span every send. The two paging models do not compose; log-projection stays the foundation |
| 50 | The MCP SDK's blessed route — `MCPServer` + `@mcp.tool()` deriving the schema from type hints (or six MCP tools, one per command) | **The low-level `Server` with the hand-authored `ToolDefinition` served verbatim**: one `memory` tool with the `command` enum, executing through the extracted `memory/dispatch.py` ladder shared with the function tool | Re-derivation would fork the schema and description the models are post-trained on from ledger #19's single definition; three transports over one definition is the point, and the drift would be silent |
| 51 | MCP `instructions` as a static string chosen once per process | **`memory_system_section(config)` rendered at construction *and* refreshed per connection in the SDK lifespan** | The per-session analogue of the frozen-index rule. Honest limit: `InitializationOptions` is built before the lifespan runs, so handshake-era clients read the construction-time render and 2026-07-28-era clients read the per-connection one — under stdio (one session per process) they are the same instant. Streamable HTTP will need a real per-connection mechanism |
| 52 | `CallToolResult` carrying `ToolResult.to_json()` — the same bytes the function tool hands the model | **Native mapping**: data/error as a text block, `system_reminder` as a second block, `is_error` on failure | MCP's spec puts tool errors in-band precisely so the model self-corrects — that *is* `ToolResult`, in MCP's vocabulary; a JSON envelope inside a protocol that already has success/error framing is double-wrapping every host would unwrap |
| 53 | The library never reads a DSN from the environment (SERVICES.md, N3) | **`neosian.mcp`'s entry point reads `NEOSIAN_MCP_POSTGRES_DSN`; there is no `--dsn` flag** | An MCP server is a process a host spawns with argv, and argv is world-readable in `ps`. The library rule is unchanged — the reader is the entry point, the same tier as the FastAPI example's `NEOSIAN_EXAMPLE_POSTGRES_DSN` |
| 54 | Eval types stay root-public at 1.0 (the v0.53 shape) | **Facade-only**: everything on lazy `neosian.evaluation`, root `__all__` −7, `EvalError` alone stays root | Evaluation is a dev-time harness — the `neosian.fake` precedent; 1.0 freezes what the root carries, and seven mutable types over a private runner was an accident about to become a promise |
| 55 | Incremental reform of the v1 module | **Full rewrite, YAML schema v2 with a `kind:` discriminator** — v1 suites break at v0.68 | Reform would have carried the mutable types, the silent-drop loader, and the `str()` scorer into the 1.0 freeze; `kind:` gives the NE memory-eval slice a first-class slot instead of a bolt-on |
| 56 | `prompts:` doubling as agent files (legacy) or variant YAMLs (variant mode) | **One `agent:` plus an explicit `variants:` axis**; the matrix is uniformly variants × models × cases | The overload made one key mean two file types and forced `is_variant_mode` branches everywhere; comparing prompt variants over one agent is the feature — comparing whole agents is a shell loop over suites |
| 57 | Absent/empty `expect:` auto-passed; a conversational top-level `expect:` was silently dropped | **`expect:` required and non-empty; strict keys at every level** — retired v1 keys (`prompts:`, `mock_response:`) fail with migration hints | Silent drops were the audited footgun class: a case that asserts nothing measures nothing, loudly refusing beats quietly passing |
| 58 | Blanket `str()` coercion in the scorer (`"None"` matched a missing param; literal `"_exists"` unmatchable) | **Typed equality + an explicit matcher vocabulary** (`equals`/`contains`/`regex`/`exists`) incl. response matchers; **no LLM judge** | Type confusion was where every false pass hid; failure messages now name types. Judge deferred by ruling — FakeProvider baselines must stay deterministic and keyless; if one ever lands, its prompt ships in `assets/` per §7 |
| 59 | `mock_agent_tools` rewrote `agent._tools` per turn; `_apply_tool_overrides` rewrote `agent._tool_definitions[i]`; `mock_response` rewrote recorded history post-hoc | **Tools are built before the agent exists** (stub by default, `execute_tools` allowlist, variant descriptions on harness-owned wrappers via `attach_tool_metadata`); `on_tool` is the single capture path; a per-turn stub table sets payloads *before* the call | Construction beats mutation: the agent is never touched after `Agent(config=…)`, the recorded history is exactly what the model saw, and library builtins (todo/playbook/blackboard/memory) always execute — `ignore_tools:` hides them from matchers |
| 60 | The runner assigned `model`/`hooks`/`client_factory`/`system_prompt` onto the loaded config, clobbering caller hooks; the agent module re-imported per cell | **`dataclasses.replace` derivation + hook composition (harness callback first, caller's after) with `strict=True`; the agent module loads once per suite** | The N2 `--menu` bug class, closed and pinned by a caller-config-untouched test; strict because a harness that swallows its own observer's failure measures nothing. Consequence stated honestly: module-level tool state is now shared across a suite's cells |
| 61 | `neosian eval` exited 0 even when every case failed | **Exit 1 when any case fails** (load errors and crashed runs stay 1) | The command is a CI gate or it is theater |
| 62 | Reusing `eval_case_invalid` for suite-level strictness and per-case `ValueError` for model typos | **Two codes appended**: `eval_config_unknown_key` (carries the v1→v2 migration hint) and `eval_model_unknown` (models validated at load) | Hosts must distinguish "you forgot a key" from "you typo'd one / you're on schema v1"; a model typo failing the suite instantly beats failing inside every cell. Append-only respected (§5, ECOSYSTEM §6) |
| 63 | Insert evaluation as §12 and renumber the ledger | **Evaluation is §13, appended after the §12 ledger** | Every existing "DESIGN §12 ledger" reference in the four docs and the commit history stays true; monotonic file order preserved |
| 64 | Kind-aware report axes for the memory eval (artifact schema 3) | **Transports occupy the `variants` axis slot**; `EvalReport`, reporter, progress, and the schema-2 artifact are untouched | `EvalReport` was built as "the seam later kinds reuse" — using it as designed costs zero, while a schema bump for a cosmetic axis label breaks a pinned host-facing shape for nothing |
| 65 | Memory-eval sessions as `Conversation`s (persistence + compaction for free) | **Bare Agents via `derive_config`**, one per session | The harness must measure the memory loop, not compaction or turn persistence; `derive_config` is the shipped wiring, so a session is wired exactly as a real one — and the edge is sanctioned in §13.11 rather than replicated |
| 66 | A base config carrying `memory=` silently overridden (`derive_config` sets `memory=None` anyway) | **Refused, as a red cell naming `mounts:`** | Per-cell store isolation is the harness's reason to exist; silently measuring against the agent's own store would be unreproducible — the silent-drop class #57 closed |
| 67 | A shipped pack containing red scenarios to prove the scoring bites | **All-green pack; discriminating negatives live in unit tests** | A shipped suite is a regression gate — green on FakeProvider by construction, so red always means a real regression; the negatives still pin that every axis of the scoring discriminates |
| 68 | A second scriptless YAML for external runs, or `script:` applied only to FAKE models | **One pack; the external tests derive scriptless configs with `dataclasses.replace`** | Model-conditional `script:` makes one key mean "sometimes silently ignored" — the class #57 closed; a second YAML forks the scenario content the fake-vs-real comparison depends on |
| 69 | Scenario stores in a discarded tmp dir | **`.neosian/evals/<ts>-memory/<transport>/<model>/<scenario>` (gitignored), the path on every red result** | A memory eval you cannot `cat` afterwards cannot be debugged; the `store root:` failure line makes the artifact self-locating |
| 70 | An `eval_store_expectation_failed` code (or an `eval_memory_*` family) | **No new codes** — scenario violations are `eval_case_invalid`, suite shape `eval_config_*`, cell-level harness failures `eval_run_failed`; store errors from mount construction re-raise in the eval family | Codes are append-only and hosts key on them the day they exist; these are new instances of four existing classes, not a new class — and a store-truth miss is a failed case, never an error |
| 71 | Keep deferring the §9.10 amendment past 1.0, or amend without the §11 SemVer flip | **Executed whole at v0.70.0**: §10 +`ConversationStore`/`ConversationStoreContract`, §6 blesses `agent_conversation_*`, §11 SemVer-guaranteed from the eventual v1.0.0; the changelog row's kit cell filled same-day by the paired kit session (kit #206, its 73dcd39) | 1.0 is the moment silence becomes commitment — an un-frozen ConversationStore seam and a "minor bump" break rule would both freeze as accidents; the kit's counterpart was recorded waiting for exactly this payload, and either repo may still refuse the pair |
| 72 | The evaluation facade recorded as an ECOSYSTEM seam at 1.0 | **Named in the eventual v1.0.0 tag annotation + README only** (user ruling, 2026-08-21): `Agent`, `Conversation`, `MemoryStore`, and `neosian.evaluation` are the promised-stable surface; ECOSYSTEM gains no evaluation section | The recorded §9.10 payload is what the kit's counterpart signed up for — extending it unilaterally breaks session-pair symmetry; evaluation is a dev-time harness (the `neosian.fake` §7 precedent covers the keyless guarantee), and a future session-pair may still seam it properly |
| 73 | Let the same-day v1.0.0 tag stand | **1.0 postponed** (user ruling, 2026-08-21): the v1.0.0 tag was withdrawn before any consumer vendored it and the release re-cut as v0.70.0; the stability promise rides the eventual v1.0.0, which waits for a stronger surface (roadmap continuation to be designed options-first — candidates: an open model registry, MCP client-side tools, an OTel exporter on the hooks, published per-provider memory baselines) | A version number is a promise, not a milestone sticker — 1.0 claimed "this is it" while the external baselines had never run and no host had vendored a tag; withdrawing same-day costs nothing, while an under-evidenced 1.0 devalues every guarantee attached to it |
| 74 | Advisory-lock the FileStore root (`flock`, lock files, leases) so several processes can share one directory | **Documented one writer per root** (NA): the in-process lock is the only arbiter; concurrent writers route to `PostgresStore` or to NM's state daemon | `supports_optimistic_concurrency = False` already says files cannot arbitrate — a lock file half-promises what it cannot keep across NFS/Windows/containers and adds stale-lock recovery to the substrate whose value is that you can `cat` it. The NA shell makes the collision reachable (an agent's CLI writing while an MCP server runs), so the constraint is stated where users meet it instead of implied by a ClassVar |
| 75 | `neosian memory` imports `_foundation/mcp/settings.py` for the store flags (crosses no contract) | **The store grammar extracted to `_foundation/memory/settings.py`** (`StoreSettings`, `add_store_arguments`, `resolve_store_settings`, `parse_mount`/`format_mount`); the MCP module keeps its prog/epilog and `ServerSettings` as an alias | The memory layer importing the MCP layer inverts §1's stack for a parser. #50's precedent is exact: when a second transport needed the command ladder, the ladder moved to a neutral module — the same move for the second argv entry point, and `format_mount` (the parser's inverse) gains a home the harness and `mcp install` reuse |
| 76 | Keep `NEOSIAN_MCP_POSTGRES_DSN` for the MCP server and mint a second key for `neosian memory` (or alias the two) | **Hard rename to `NEOSIAN_POSTGRES_DSN`** — one key for every argv entry point; the old name removed same-commit, no alias (user ruling, 2026-08-21) | The key stopped being MCP-specific the moment a second entry point read it; two names for one DSN is a second host-visible contract to keep true forever, and an alias needs a conflict rule. Pre-1.0, private repo, no consumer has vendored the MCP server — the deliberate-batched-break precedent (#54's schema v2). #53's substance (no `--dsn`, entry-point-tier reader) is unchanged |
| 77 | `neosian memory --json` maps `ToolResult` to a CLI-native shape, as the MCP transport did (#52) | **`--json` prints `ToolResult.to_json()` verbatim** — one line on stdout, exit 0/1 by `success`; human mode prints `data` on stdout, `error:`/`hint:` on stderr; argv-tier errors stay argparse text at exit 2 | #52 held because MCP has in-band success/error framing; a shell has only an exit code, so the function tool's envelope IS the machine surface — a second wrapper would be a shape to document and drift. Printing it verbatim is also what lets the harness's cli transport parse a real `ToolResult` back out, measuring shipped bytes instead of re-deriving them |
| 78 | The harness's `cli` transport spawns the real `neosian memory` binary per memory call (the literal reading of "the shell is a transport") | **The engine runs in-process** — argv built per call, a fresh store from `--root` per invocation, the `--json` envelope decoded back; the process boundary is pinned once per gate by the keyless walkthrough driving all six commands through the literal binary (user ruling, 2026-08-21) | What can drift is the grammar, the envelope, and the exit tiering — all crossed in-process; the fork/exec layer is scenario-independent, so re-crossing it per cell buys no coverage the walkthrough lacks while taxing every `make test` ~10 interpreter starts, blinding coverage, and trading tracebacks for exit codes. Measurement precision is the user-facing asset here (§13.12 is the benchmark seed) |
| 79 | `[tool.hatch.build.force-include]` mapping the repo-root `llms.txt` into the wheel | **Two byte-identical files** — the repo root and `assets/llms.txt` — pinned by one unit test; no build config. A second test pins the install-pin tag to `__version__`, so a release bumps `llms.txt` same-commit or the gate goes red | The repo has no hatch build section, and the first force-include opens "what else needs one" for every future root artifact, making wheel contents build-config-dependent and invisible in the tree; two files and one assertion are greppable and cost nothing. The version coupling is the stale-README-pin failure mode (found at NA: the v0.70.0 pins survived a release), closed structurally |
| 80 | `mcp install --write` creates a missing client config directory (`mkdir -p`, the friendly move) | **Refuse at exit 1**, naming the path; never `mkdir`. Same discipline one level down: an unparseable config file, or an `mcpServers` that is not a JSON object, is refused — never rewritten | A missing config home means the client is not installed here — an environment fact, not a grammar mistake (§14.1's tier 1). Creating another program's config directory guesses at a layout neosian does not own and leaves debris when the guess is wrong; "preserve every unknown key" is only true if what cannot be parsed is also never touched |
| 81 | `claude-code` registers at `~/.claude.json` (user scope); the entry's `command` is the `neosian` console script found on PATH | **Project `./.mcp.json`** with `~/.claude` as the installed-client evidence; **`command` = the current interpreter's absolute path** (`sys.executable`) + `-m neosian.mcp` (user rulings, 2026-08-21) | A memory root is usually project-shaped and `.mcp.json` travels with the repo the agent works in (Claude Code approval-prompts it natively); GUI-launched clients do not inherit a shell's PATH, so an absolute interpreter is the only registration that works from Claude Desktop, and `-m` keeps the entry independent of console-script naming. Both choices live in one table row / one parameter, so a reversal is one-line cheap |
| 82 | The shipped pack pins exact document paths (`/user/preferences`); the first real runs (NV, 2026-08-21) went red on all four providers while the models filed facts correctly under their own names (`/user/preferences.md`, `/user/drink.txt`) | **`path_prefix` document expectations** (user ruling): exactly one live document under the prefix satisfies `content` — zero is the fact unrecorded, two is the duplicate; `versions`/`actions` apply to the matched document. The pack reshapes onto it; history pins stay only where the disciplined path is unambiguous (first writes, recall's no-new-writes); exact `path:` keeps its strictness in the unit-tier negatives | Nothing in the prompt pack names documents, so exact paths measured an unspecified convention — publishing those reds as "failed write discipline" would be the motivated-reasoning mode ROADMAP's benchmark-honesty risk names, inverted. The pins that were communicated (mount routing, no secrets, update-not-duplicate, delete-what-proved-wrong) all survive, and the negatives prove the scoring still bites on each |
| 83 | OTel via a wrapper/middleware layer, or spans nested by buffering events per run until `on_turn` | **`neosian.otel.otel_hooks()` → a plain `AgentHooks`** (facade-only surface, root `__all__` untouched); flat post-hoc spans, one per hook event, `start = end − duration_ms`; gen_ai semconv names + `neosian.*`; names/models/token-counts/outcomes only — never message content, tool arguments, or results; extra = `opentelemetry-api` only (SDK in the dev group solely for the in-memory exporter tests) | Hooks fire after the observed work with its wall time — flat spans state exactly what the seam knows, while parenting would require correlating events that carry no run identity and holding state in the observer; payload-free spans keep telemetry from becoming a second store of user data; api-only keeps the extra dependency-light since the host owns the SDK/exporter choice |
| 84 | A fourth provider (Groq) in the registry, with guardrails hard-wired to it: a raw SDK client, the policy model a constant only that provider hosts, guardrail spend invisible, the checker unreachable from FakeProvider | **The provider exits the registry (NW step 0, user ruling — membership is baseline-gated, BASELINES.md holds the measured evidence) and guardrails re-platform**: `GuardrailsConfig.model: Model \| None` runs the classifier prompt through a neosian client from `Agent._create_client` (the #32 seam, honoring `client_factory`), `None` = the agent's own model; no explicit temperature (some models reject non-defaults); a missing key for the guardrail provider raises at construction, never a silent fail-open at check time. `AgentConfig.model` default → `CEREBRAS_GPT_OSS_120B`. Guardrail usage folding into run accounting stays deferred | The safeguard model had no other host, so the exit forced the re-platform the design owed anyway: guardrails carried a hidden second-provider key dependency (the exact coupling that made the exit painful) and could not be tested keylessly; via `_create_client` they ride the fakes like every other seam. Deleting a provider is honest only whole — client, enum rows, pricing, CI lane, suites — never a half-maintained stub |
| 85 | Reflection triggered by `aclose()` alone, by the compaction boundary, or only by an explicit call; opt-in default | **Public `Conversation.reflect()` (the `compact()` shape) plus a default-on `aclose()` rider** (NR, user ruling): `ReflectionConfig(enabled=True, model=None)` resolves like CompactionConfig, `enabled` gates the rider only, explicit `reflect()` always runs; the pass covers this instance's unreflected sends and is skipped when there are none; a degraded model call leaves them pending for a retry | `aclose()` alone is unreliable — crash paths never reach it, §9.5.14 blesses never closing, and a per-request host (the FastAPI example) closes per *turn*; the compaction boundary is mid-session paging, not a session boundary. Default-on completes the behavior a memory-bearing conversation already opted into; per-request hosts set `enabled=False` and call `reflect()` at their real boundary |
| 86 | A prefixed reflection actor (`reflect:<id>`), or a pre-write approval callback | **Receipts, no gate**: `ReflectionResult` lists every landed write (command, path, live version); `actor = conversation_id`, identical to in-session writes; version rows are the undo substrate | The phase text's own audit wording; a reflection-specific approval seam would pre-empt NT's gate design, and host-visible write events + undo are NP's deliverable — nothing here may freeze their shape early |
| 87 | Reflection as a second legitimate memory-index refresh point | **No new refresh point** — §9.5.10's boundary stays the only one; reflection writes surface in the *next* conversation's frozen index, and spend rides the returned `ReflectionResult` (`aclose()` now returns `ReflectionResult \| None`) instead of folding into any send | Refreshing at close buys nothing (the instance is ending) and mid-session `reflect()` refreshes would invalidate the prompt cache without a compaction boundary's justification; the frozen-index rule already defines writes-surface-next-conversation as correct |
| 88 | Harness sessions become Conversations so reflection fires through the real `aclose()` | **Runner-level reflection** (#65 stands): `MemorySession.reflect` runs the same `run_reflection` engine over the session transcript after the turn loop, actor = the eval actor, acquire = `Agent._create_client` (the #32/#84 seam, honoring `client_factory` — scripted cells consume the script's next turn as the reflection response); the encapsulation pin gains the `._create_client` token with its sanctioned callers | Sessions-as-bare-Agents is what makes cells cheap and store truth the arbiter; the engine — not the trigger plumbing — is the measured behavior, and the trigger is pinned separately in the unit tier |
| 89 | The weekly external-provider CI schedule (Mondays 06:00 UTC, standing since NV) | **External runs are dispatch-only** (user ruling, 2026-08-21): the `schedule:` trigger is removed; baseline re-runs and the NW membership re-tests happen as deliberate `workflow_dispatch` acts — NW's per-candidate done-when reads "two consecutive external runs", cadence chosen by the operator | A standing weekly real-API bill is a cost commitment the project does not want automated; every run of the pack is meaningful only when someone reads it, and the fingerprint gate already forces a recorded run at every pack change — the re-test happens when it is owed, not on a timer |
| 90 | Maintenance triggered by a Conversation boundary rider beside reflection (opt-in or default-on) | **Explicit only** (NG, user ruling 2026-08-22): the public `run_maintenance` + the `neosian memory maintain` verb; no Conversation rider of any kind | Maintenance is scope-wide work at operator cadence, not session-scoped: a close-time rider would re-judge the whole store every close (churn plus a second, store-sized model call), reflection already owns the session boundary, #89's spirit applies — recurring spend is a deliberate act — and #87 means mid-session writes only surface next conversation anyway |
| 91 | One authority: the model rules every mutation, the deterministic layer only proposes (or: deterministic + a mandatory model stage) | **The deterministic stage executes the byte-safe ops itself** (NG, user ruling 2026-08-22): byte-identical duplicate merge (keep the earliest `created_at`, ties to the first path) and empty-document prune — `neosian memory maintain` has a working keyless mode; the model stage (semantic merge, stale pruning, promotion, confirm-or-decay) runs only when a model is given | "Deterministic-first" made literal: exact-content equality needs no judgment, the memory CLI is keyless everywhere else, and the model pass stays an opt-in spend the operator chooses |
| 92 | Protection by prompt alone (only the structural read-only exclusion), or adding a protected-prefix config | **Age floor + redacted skip, enforced in code** (NG, user ruling 2026-08-22): documents updated inside `min_age` (default 7 days, Clock-injectable) are never *deleted* by either stage — `create`/`str_replace`/`rename` stay legal — and redacted documents take no operation at all; read-only mounts are structurally excluded (the §15 precedent); pinned prefixes declined | Fresh facts haven't had time to prove wrong; a redacted document reads back empty, so an unguarded empty-prune would eat exactly what C3 promised to preserve; a protected-prefix list is a config surface with no demonstrated need — NP's governance discussion is its natural home if one arrives |
| 93 | The content-matcher judge built inside NG (its re-run would exercise it), or declined outright | **Shape affirmed now, implementation at NC6** (NG, user ruling 2026-08-22): §13.13's reserved shape is the ruling — opt-in, external tier only, never keyless, prompt as assets data; NG's baseline re-run stays deterministic | Runs 1–3's pattern (every content-level red was a pin narrower than legitimate behavior; the structural checks only ever caught real signal) says the judge is owed, but NC6's external-yardstick session shares its driver and spend policy — one build, two consumers, and the keyless tier stays deterministic forever |
| 94 | The index budget as a `MemoryConfig` field (per-agent tuning everywhere), or a bare unconfigurable constant | **A `budget_chars` keyword on the one renderer, default module-local `INDEX_BUDGET_CHARS = 8192`** (NG slice B, user ruling 2026-08-22): §9.6's tiers applied to the index — hot doc lines newest-first, per-directory fold lines, a mount total as the floor; byte-identical under budget; `view` of a folded directory is the re-hydration | The #92 discipline: no config surface without a demonstrated need — every shipped consumer rides the default, a host calling the renderer directly can tune, and a `MemoryConfig` field is one-line cheap the day a real deployment asks. Promotion is a recency cutoff, never a knapsack, and the accounting shares the renderer's line helpers so a promotion that shrinks the render (a fold line can outweigh a doc line) still lands |
| 95 | Seeds as raw `store.write` fixtures, or a fixed backdating with no knob | **`seed:` entries `{path, content, age_days?=30}` written through the shipped dispatcher, actor `eval:seed`, on a clock set `age_days` back** (NG slice B, user ruling 2026-08-22) | Dispatcher writes keep seeds audited and mount-validated like every other transport's; real backdated timestamps make the maintenance floor measurable instead of mocked, and `age_days: 0` plants the fresh-document protection case in external runs too — the NV note ("populating a 500-doc store one scripted create at a time is not a scenario") resolved as data, not code |
| 96 | Every session keeps a mandatory turn list (a maintenance step carries a throwaway turn) | **`maintain:` as a session key mirroring `reflect:`, with `turns:` optional only there** (NG slice B, user ruling 2026-08-22): order turns → reflect → maintain → store truth; a turn-less step gets one synthetic passed turn; `reflect:` without turns is refused | The gardener is the measured behavior — a filler turn would put an unmeasured model exchange inside the cell; the synthetic turn is bookkeeping (store truth needs a turn to land on), and the engines share the `._create_client` acquire seam #88 already sanctioned for this file |
| 97 | Strengthen memory.yaml alone (the roadmap carry's letter) | **All three prompt assets in one fingerprint batch** (NG slice B, user ruling 2026-08-22): the shared no-secrets vocabulary (secrets, credentials, API keys, tokens — "not even to note that one exists", the asked-to-forget clause), reflection.yaml's rule stands alone naming transcript secrets, maintenance.yaml aligned; the preventive guardrail stays NP's | Run 3's stored token was a *reflection-boundary* write — the offending call's only prompt input was reflection.yaml, where the rule sat mid-paragraph; strengthening only memory.yaml would have polished the prompt the failure never read. Three files, one gate, one re-run |
| 98 | Receipts stringified only (`"Created /x (v3)"`), a widened dispatch return type, or a receipt every transport serializes | **`MemoryWriteReceipt` rides `ToolResult.receipt`, a field `to_json()` ignores** (NP, 2026-08-22): `commands.py` — the one place that holds mount, virtual path, command and store-assigned version together — attaches it on every successful mutation; `dispatch` stays `-> ToolResult[str]`; the delete receipt's version comes from `versions(limit=1)` (the ABC returns `bool` and `read` is None after a delete); rename = one receipt, `path`=dst, `previous_path`=src, both compositions | The wire envelope is frozen across transports (#77's byte-parity) and the receipt's consumers are all in-process — the event emission, reflection/maintenance result rows (their `_live_version` re-reads deleted), and `revert_memory`'s prose; out-of-process transports drop it by construction, which is correct: events exist only on `run(stream=True)` |
| 99 | Five per-command events, per-version-row events, or enriching `tool_result`'s frozen payload | **One `memory_write` frame per successful mutating command** (NP, user ruling 2026-08-22): emitted in `tool_exec` immediately after that call's `tool_result`, payload `{command, path, version, tool_call_id, previous_path}` — never content; `view`, failed calls and off-stream writes (reflection, maintenance, redaction) stay silent; the ECOSYSTEM §5 vocabulary grows nine → ten as this arc's one deliberate seam change | Hosts want the command-level act ("remembered X", one undo argument), not storage mechanics; a content-free frame cannot leak what was written by construction; redaction never happens inside a run, so a redact event would be a dead letter in a frozen vocabulary |
| 100 | Per-send `_rebuild_agent` + `_rebind` to stamp a per-turn actor | **Late-bound actor: `create_memory_tool(actor=)` accepts `str \| Callable[[], str]`** (NP, user ruling 2026-08-22): Conversation passes `_turn_actor` — `<conversation_id>#<turn>`, read under the send lock (`#` is illegal in conversation ids); reflection keeps the bare id (#86); CLI/MCP/eval actors unchanged; a failed send persists nothing, so its number is reused | A per-send rebuild would construct (and leak) a guardrail client per send, re-run `AgentConfig.__post_init__`'s `playbook_dir` disk read per send, and violate §9.5.10's one-index-refresh-point rule — the closure delivers durable per-fact turn-refs for the price of a lambda, and the store still receives one opaque string |
| 101 | A seventh dispatch command (`undo`), a Conversation-only method, or raw store restores | **`revert_memory(config, path, version=, actor=)` — a public function executing inverse ops through the shared dispatcher** (NP, user ruling 2026-08-22): version names the row to *undo* and must be the newest (`memory_conflict`, reason `revert_stale`, otherwise); one inverse rule — no live document before row N → delete, else row N-1's content back; redacted history refuses; the success receipt is re-badged `command="revert"`; a `neosian memory revert` operator verb follows in slice B; never registered on an agent, never MCP | Undo is host/operator territory — hosts render the button, models do not erase their own trail (the maintain precedent, §14.2); dispatch execution keeps read-only enforcement, corrective mapping and the audit row; append-only reverts keep C3/C5's receipts intact, and refusing a stale target beats silently destroying later writes |
| 102 | The no-secrets write guardrail: a deterministic pattern gate at dispatch (structure-anchored rules), or a model-based pre-write classifier | **Prevention declined — remedy-first** (NP, user ruling 2026-08-22): no preventive gate ships; the governance story is prompts discourage (#97), the eval detects (`forbidden`), and NP makes the remedy first-class — `memory_write` receipts, find-and-redact (slice B), undo; a model-based guard stays available to a future phase if evidence demands it | Deterministic secret detection was ruled error-prone as a class (user); a model check cannot run at the client-free dispatch chokepoint without threading a client through every transport, and version rows + scope-wide `redact()` mean the failure mode is recoverable — the phase bullet closes by deliberate declination, never by silence |
| 103 | Edit-only as create-new-blocked only (delete/rename stay legal), a mode enum replacing `read_only`, or prompt-level discipline without enforcement | **Fixed document set** (NP slice B, user ruling 2026-08-22): `Mount.edit_only` (second bool, exclusive with `read_only` — plain ValueError) refuses create-of-a-new-path, `delete`, and `rename` touching the mount on either side via `structural()` — the second tool-layer predicate beside `writable()`, raising the appended `memory_edit_only_mount`; overwrite-create, `str_replace`, `insert` and `redact` stay legal (content acts). Argv token `eo`; `(edit-only)` index marker; reflection shows the mount annotated, maintenance's deterministic stage skips it and `_fixed_set` names refusals; prompt YAMLs untouched (no fingerprint trip); the eval mount schema deliberately unextended (unit-tier negatives, the #67 idiom) | "Pre-created layouts the agent may edit but not extend" is a *set* guarantee — delete or rename-away breaks a layout as surely as create grows it, and the half-rule would leak documents out of curated mounts. Existence-gating `create` (the read it already does) is what keeps every dispatch consumer — revert's restore, reflection and maintenance edits — working unchanged; the one bite (undoing a pre-`eo` created row) is pre-`eo` history only, escaped by re-mounting without `eo` |
| 104 | Operator verbs as dispatch commands, a separate `neosian audit` binary, or `redact` document-only / unguarded scope-wide | **`versions`/`redact`/`revert` join `maintain` on the verb side** (NP slice B, user ruling 2026-08-22): keyless, never in `ARGUMENT_KEYS`, own `--json` envelopes; `versions --json` carries full row content (point-in-time reads exposed) while text output never does; empty history exits 0; `redact PATH` for a document, mount root + explicit `--all` for the whole scope (grammar-tier string check, exit 2 without it; output names the scope — mounts can share one); tier-1 failures under `--json` print one minimal `{"error", "hint"}` object so §14.1's promise stays literal; `--limit`/`--version` < 1 refused at the grammar (the stores raise bare ValueError past `run()`'s catch). cli.py split into grammar/maintain/operate/store-lifetime modules (the size gate) | The six-command vocabulary is the frozen agent-facing interface; audit and erasure are operator acts (#101's maintain precedent) — and keeping them out of the dispatch table is what keeps an eval-cell model from performing a real redaction. `--all` is the two-token deliberate act the one irreversible verb deserves, while staying fully non-interactive (agent-native: the refusal names the flag) |

## §13 Evaluation (NE)

### §13.1 What an eval is

One agent under test, measured over a **variants × models × cases** matrix.
A *variant* is a prompting strategy (system prompt + tool-description
overrides); a *case* is one or more user turns with typed expectations. The
harness observes runs through `AgentHooks` — it never alters, wraps, or
reaches into the agent it measures.

### §13.2 Schema v2 and the `kind:` discriminator

Suites are user-supplied YAML (§7 governs only prose the library *ships*).
Top level: `kind` (optional, default `agent`), `name`, `agent` (path to a
Python file exporting `configuration`), `models` (validated at load;
`provider:model` or bare model value), `cases`, and optional `variants`
(`[{name, prompt}]`), `execute_tools`, `ignore_tools`, `stop_on_failure`
(default true), `throttle_ms` (default 500). A variant prompt file carries
`system_prompt` + optional `tools: {name: {description}}`.

**Strict keys at every level** — unknown keys raise, retired v1 keys
(`prompts:`, `mock_response:`, `on_success:`) raise with migration hints
(ledger #57). `kind:` discriminates config shapes and is read before any
other key — every other key is kind-shaped. Two kinds exist: `agent`
(this section) and `memory` (§13.12); an agent-kind key on a memory
suite fails with a targeted hint, and vice versa.

Cases: `input:` + `expect:` is sugar for a one-turn `conversation:`; both
together is an error, and a top-level `expect:` beside `conversation:` is a
loud `eval_case_invalid`. Every turn carries a required non-empty `expect:`
and may carry `tool_results:` (per-turn stub payloads, §13.6). `script:`
runs the case keylessly (§13.7). A case-level `execute_tools:` extends the
suite's allowlist.

### §13.3 Types and the module map

Every config and result type is `@dataclass(frozen=True, slots=True)`,
module-local under `_foundation/evaluation/` — nothing eval-shaped lives in
`shared/types.py` (the v1 block there is deleted). `types.py` holds the
config vocabulary (`AgentEvalConfig`, `EvalCase`, `EvalTurn`, `Expectation`,
`ValueMatcher`, `SequenceStep`, `Variant`, `EvalKind`, `MatchMode`, the
`type EvalConfig` union alias); `results.py` the result vocabulary
(`EvalReport`, `CaseResult`, `TurnResult`, `ToolCallCapture`,
`ProgressEvent`). The pipeline: `loader.py`/`cases.py`/`expectations.py`/
`variants.py` parse; `matcher.py` scores; `stubs.py` builds tools;
`runner.py` runs one cell; `matrix.py` runs the suite; `progress.py`/
`reporter.py` present. `schema.py` holds the kind-neutral parsing
primitives and `capture.py` the shared observation seams (hook
composition, tool capture, scripted factories); the memory kind adds
`memory_types.py`/`memory_loader.py`/`memory_expectations.py`/
`memory_score.py`/`memory_runner.py`/`memory_cli.py` (§13.12; the last
is the cli transport's tool, §14.3). `EvalReport` carries its own axes, so
presentation never needs the config type — the seam later kinds reuse;
its config-side twin is the three axis-name properties
(`variant_names`/`model_names`/`case_names`) every config type carries,
which are all `progress.py` reads.

### §13.4 Matching semantics

Typed equality (ledger #58): bool compares only to bool (`True != 1`), str
only to str (no coercion), int↔float compare numerically, containers
compare elementwise/key-set-exact. Matcher modes: `equals` (typed),
`contains` (substring, or list element by typed equality), `regex`
(compiled at load — a bad pattern fails the config, never a run), `exists`
(present and not None; `_exists` is sugar). A one-key mapping over the
matcher vocabulary is a matcher; any other value is an `equals` literal
(the documented escape for a literal one-key `{equals: …}` value is
`{equals: {equals: …}}`).

Tool rules: `tool:` requires the **first** non-ignored call to be the
expected tool (params match on it; later calls are unconstrained — use
`sequence:` to constrain them); `sequence:` is exact names, order, and
length; `no_tool:` fails on any visible call. `response:` is an explicit
one-key matcher mapping (never a bare string); `contains`/`regex` accept a
list for all-of. One deliberate asymmetry: response `contains` is
case-insensitive — prose casing is model noise — while param `contains`
stays exact, because arguments are structured data. Failures accumulate;
`TurnResult.failures` reports every unmet expectation with types named.

### §13.5 The runner

`run_case` derives the cell's config with `dataclasses.replace` — model,
variant prompt, harness-built tools, composed hooks, scripted client — and
constructs the agent once; the caller's config is never written and caller
hooks are composed, never clobbered (harness callback first, caller's
after), always `strict=True` (ledger #60). A non-sticky fallback fails the
case: an eval measures the configured model. `run_evaluation` loads the
agent module once per suite, dispatches on the config's kind (a memory
suite's cell is a scenario, §13.12), throttles real-API cells only, and
downgrades a cell's `EvalError` to a failed `CaseResult` — one bad case
never kills the suite; an agent that cannot load does, since nothing
could measure anything. Harness-level failures raise `eval_run_failed`
with the cell's variant × model × case context.

### §13.6 Tools under evaluation

Stub by default, execute by allowlist (ledger #59). `stubs.build_tools`
runs before the agent exists: a user tool becomes a wrapper returning a
canned success — or the turn's `tool_results` payload, set *before* the
model call, so the recorded `turn_messages` thread verbatim — unless named
in `execute_tools`, in which case the original function passes through by
identity. Variant description overrides ride the same construction:
wrappers carry a `dataclasses.replace`d definition (name/parameters/strict/
`native_type` survive) attached via `tools.base.attach_tool_metadata`, the
`set_native_type` idiom (ledger #41). Library builtins registered from
config fields (todo/playbook/blackboard/memory) always execute and are
invisible to `execute_tools`; `ignore_tools:` hides any name from matchers
while still capturing it. Unknown names in either list are errors — a typo
would silently measure the wrong thing.

### §13.7 Keyless runs

A `script:` case parses into `FakeTurn`s and runs on one scripted
`FakeClient` injected via `client_factory` — one instance per case, so the
script cursor survives multi-call tool rounds; an explicit script wins over
a caller-provided factory. Scripted and FAKE-model cells skip the
throttle. The unit tier exercises the whole harness — loader to artifact —
with zero keys.

### §13.8 Results, report, artifact

`run_evaluation` returns a frozen `EvalReport`; `print_report` renders
per-model tables (rows = variants, columns = cases) plus summary and
failure detail; `save_report` writes `.neosian/evals/<ts>.json` with
`schema: 2`, the axes, a summary, and per-turn detail — the rendered
expectation, accumulated failures, response text, and every capture with
`executed`/`ok`/`duration_ms`.

### §13.9 Public surface and the CLI contract

The facade is `neosian/evaluation/__init__.py` — re-exports only, never
imported by the root package (ledger #54); pinned by
`tests/unit/test_evaluation_exports.py` incl. a subprocess laziness pin.
The export rule: **everything reachable from an exported config type is
itself exported** — an exported field typed by a private class would
freeze asymmetry by silence, the accident NE exists to kill. The memory
kind added six names (39 total): `MemoryEvalConfig`, `MemoryScenario`,
`MemorySession`, `StoreExpectation`, `DocumentExpectation`, `Transport`.
`neosian eval <suite.yaml>` rides the facade, prints the report, writes the
artifact, and **exits 1 when any case fails** (ledger #61) — a CI gate.
Credential loading from the CLI config stays the CLI's job.

### §13.10 Error codes

The `eval_` family (§5, append-only): `eval_config_not_found`,
`eval_config_invalid_yaml` (unparseable *or* structurally invalid),
`eval_config_missing_key`, `eval_config_unknown_key` (NE; carries the v1→v2
hint), `eval_model_unknown` (NE), `eval_prompt_not_found` (a variant's
prompt file), `eval_case_invalid` (every case/turn/expect violation),
`eval_run_failed` (harness-level cell failure), over the `eval_error` base.

### §13.11 Encapsulation and layering pins

`tests/unit/evaluation/test_encapsulation.py` pins that the private tool
tokens (`._tools`, `._tool_definitions`, `._tool_metadata`) are touched
only by the agent/tools modules that own them — `evaluation/` appears
nowhere. Two import-linter contracts (§1): the runtime library never
imports the harness, and the harness never imports provider client
modules. The harness's sanctioned runtime seams are `llm.fake`, the
`Agent`, and — for the memory kind — `conversation.wiring.derive_config`
plus the memory layer (§13.12): the harness measures the shipped wiring,
never a replica of it.

### §13.12 `kind: memory` — the memory eval harness

The library measuring its own memory layer: **write discipline /
recall-in-next-session / dedup**, as a **transports × models ×
scenarios** matrix. `MemoryEvalConfig` widens the `EvalConfig` alias and
one dispatch branch in `run_evaluation` routes it — slice A's shapes did
not move. There is no public benchmark for this regime (LoCoMo measures
personalization), so the shipped pack is the yardstick.

Suite shape (strict keys per §13.2): `agent` names an AgentConfig
**without `memory=`** — the suite owns the store and the `mounts:` list,
one fresh FileStore root per cell; a base config carrying `memory=` is
refused as a red cell naming `mounts:`, never silently overridden
(ledger #66). `transports:` (default `[function]`) is `function`,
`native_memory`, or `cli` (NA, §14.3): the native marker is ledger #43's
promised comparison and degrades by construction off Anthropic (#44), so
that axis is informative only on Anthropic runs; `cli` executes every
memory call through the `neosian memory` engine in-process
(`memory_cli.py`, ledger #78) and is provider-independent — the shipped
pack runs `[function, cli]` keylessly. A `scenario` is ordered
`sessions` plus an optional `seed:`; a session is
`{name, turns?, script?, expect_store?, reflect?, maintain?}` with turns
reusing §13.2's turn shape, `script:` per session (all-or-none per
scenario), and `expect_store:` evaluated after the session's last turn.
`reflect:` runs the §15 engine over the session transcript after the
turn loop; `maintain:` runs the §16 gardener after it — `turns:` is
optional only on a maintain session (a pure gardening step, which gets
one synthetic passed turn so store truth has a place to land), and
`reflect:` without turns is refused (an empty transcript reflects
nothing). Each engine call consumes the script's next turn in scripted
cells and rides `Agent._create_client` with the cell's model otherwise
(§13.11's sanctioned reach).

**`seed:` — documents that exist before session 1 (NG).** A scenario
lists `{path, content, age_days?: int ≥ 0, default 30}` entries, each
written through the shipped dispatcher under actor `eval:seed` on a
store whose clock sits `age_days` in the past — seeded documents carry
real aging evidence (old enough for maintenance's deletion floor by
default; `age_days: 0` plants a deliberately fresh, protected one).
Paths must name a document inside a declared mount, duplicates are
refused, and a refused write is a harness error, never a scenario miss.
Populating a 500-document store one scripted `create` at a time is not
a scenario — that was the NV note this key resolves.

**Sessions are bare Agents built via `conversation.wiring.derive_config`**
(ledger #65) — the shipped wiring, so a session gets the actor-bound
tool (`actor = eval:<scenario>:<session>`, visible on every version
row), the prompt-pack section, and a freshly rendered index per session:
the frozen-index rule is what makes recall honestly measurable only in
session 2. The memory builtin really writes its store (§13.6); each
session gets its own scripted FakeClient (the §13.7 cursor rule, per
session instead of per case).

**Turn expectations stay loose; store truth carries the strictness** —
the design's thesis. Four predicates: `documents` (existence, `content:`
matchers with response semantics — model-authored prose, §13.4 —,
`versions` exact count, `actions` oldest-first), `counts` (exact live
documents under a prefix — the dedup signal), `absent`, and `forbidden`
(no live document anywhere contains the text — the no-secrets rule).
A `documents` entry names its target by exactly one of `path` (this
exact document) or `path_prefix` (exactly one live document under the
prefix satisfies `content` — zero is the fact unrecorded, two is the
duplicate; `versions`/`actions` apply to the matched document). Document
*naming* is legitimately the model's, so model-driven scenarios pin the
prefix — the first real runs went red on names alone (ledger #82) —
while scripted suites keep exact-path strictness. Failures are
`store: `-prefixed and name the offenders; scoring re-reads through a
freshly constructed store, so a pass is an on-disk truth.

Scenario stores land under
`.neosian/evals/<ts>-memory/<transport>/<model>/<scenario>` (segments
slugged; `run_evaluation(store_root=)` overrides, the `save_report`
precedent) and every red result carries a `store root:` failure line —
a memory eval you cannot `cat` afterwards cannot be debugged (#69).
The report is unchanged: transports occupy the `variants` axis (#64),
so reporter, progress, and the schema-2 artifact never learn about
kinds.

`examples/eval_memory_baseline.yaml` is the shipped pack: all-scripted,
keyless, all-green by construction — a regression gate (#67); the
discriminating negatives live in unit tests. The external baselines
(`tests/external/cross/test_memory_baselines.py`, per provider,
dispatched on demand — ledger #89)
derive scriptless copies of the same pack in code (#68) — one source of
scenario truth. Honest limits: the transport axis differs only on
Anthropic, and FileStore is the only substrate this harness measures.

### §13.13 No judge

Ruled out for NE (ledger #58): scoring is deterministic — matchers here,
store truth in the memory slice. If an LLM judge ever enters, it is opt-in,
never in the keyless tier, and its prompt ships as `assets/` data per §7.

**NG ruled the reserved shape (2026-08-22, ledger #93):** the
content-matcher judge for the *external* tier is affirmed — every
content-level red across runs 1–3 was a pin narrower than legitimate
behavior, while the structural checks (counts, forbidden, versions,
exactly-one-match) only ever caught real signal. It stays opt-in, never
keyless, prompt as assets data; the implementation lands with NC6's
external-yardstick session, which shares its driver and spend policy.
The keyless tier stays deterministic forever.

## §14 The agent surface (NA)

Agents are becoming the package-choosers: a library an agent cannot
discover, learn, and operate from inside a shell sandbox loses to a
worse one it knows from training. NA builds the two doors an agent walks
through first — the shell and the docs — to the standard of the runtime
transports. Machine-shaped, never agent-flavored: subcommands, not a
chat REPL.

### §14.1 The shell contract

Every `neosian` command is fully non-interactive, and every data-bearing
command takes `--json`. **stdout carries the artifact; stderr carries
guidance** — `error: …` and `hint: …` lines, so redirecting stdout
always captures something well-formed. Exit tiers, everywhere:

- **0** — success.
- **1** — the command ran and failed: a corrective dispatch failure
  (rendered with its existing `[code]`), a missing extra, an
  environment error.
- **2** — the invocation was wrong: grammar, an unknown name, an
  invalid scope or mount. Nothing is constructed on this tier.
- **130** — interrupt (the entry tier's `KeyboardInterrupt`).

`--json` governs the executed tiers (0/1): exactly one JSON object on
stdout, nothing else. Argv-tier errors (exit 2) stay argparse text on
stderr — a shell answers grammar before any envelope exists, and that
asymmetry is the tiering, not an inconsistency. CLI usage failures mint
**no new error codes** (§5's registry is a host contract, closed to
plumbing); when a store error is the cause, its existing code rides the
error text.

### §14.2 The memory CLI

The shell is the fourth transport over `memory/dispatch.py` — after the
function tool, the native `memory_20250818` declaration, and the MCP
server — so an agent with nothing but shell access operates the same
memory the runtime transports serve. `neosian memory <command>` is a
verbatim typer pass-through (the `mcp` shape) to the entry tier
`neosian/memory/cli.py`, which owns `asyncio.run`, the real streams and
the store's lifetime (#33's rule); `python -m neosian.memory` is the
sandbox-safe twin for a venv whose bin is not on PATH. The engine is
`_foundation/memory/cli.py` — async and stream-injected, which is what
lets the eval harness call it in-process from a running loop — split
(NP slice B, the size gate) into `cli_grammar.py` (pure parsing; exit 2
constructs nothing), `cli_maintain.py` / `cli_operate.py` (the verb
execution tiers) and `store_lifetime.py` (the one DSN-vs-root branch as
an async context manager).

The grammar: `view [PATH] [--view-range START END]` (PATH defaults to
`/`, so the first command an agent tries works with store flags alone) ·
`create PATH --content TEXT` · `str_replace PATH --old-str T --new-str
T` · `insert PATH --insert-line N --insert-text T` · `delete PATH` ·
`rename OLD NEW`. Flags are the dispatcher's argument names,
kebab-cased — the argv→arguments mapping is a key list, not a
translation table. `-` as the value of `--content`/`--new-str`/
`--insert-text` reads stdin (the heredoc idiom; exactly one payload
flag per command). There is deliberately **no `--file-text` alias**: it
exists in the dispatcher to absorb the native tool's *trained* emission,
and there is no trained shell emission to absorb. The store flags are
the shared grammar of `memory/settings.py` (#75) with
`default_actor="cli"` — `--actor` passes verbatim (opaque, like `mcp:*`
and `eval:*`; `cli:<host>` is the documented convention), the mount
token is `scope=...,path=...[,ro|,eo]` (`ro` read-only, `eo` edit-only;
mutually exclusive at the grammar tier) — and Postgres arrives only
through `NEOSIAN_POSTGRES_DSN` (#53/#76). `--json` prints
`ToolResult.to_json()` verbatim (#77). The CLI is the transport §8's
one-writer rule (#74) exists for: an agent's shell writing a root while
an MCP server serves it is exactly the collision the rule routes to
Postgres or the daemon.

**The `maintain` verb (NG, §16)** is deliberately *not* a seventh
dispatch command — the six-command vocabulary is the frozen
agent-facing interface, while gardening is an operator act. `neosian
memory maintain [store flags] [--model MODEL] [--min-age-days N]` runs
the maintenance engine over the writable mounts: keyless by default
(the deterministic stage), `--model` adds the semantic pass. Its
`--json` prints the engine's own envelope —
`{"writes": [...], "model", "usage", "cost_micro_usd"}` — never the
six-command `ToolResult` shape. Exit tiers hold: an unknown model or
negative `--min-age-days` is grammar (2, nothing constructed); a
missing provider key is caught eagerly at client construction (2, the
#84 rule — never a silent degrade); a requested model stage that
degrades exits 1 *and says so* on stderr while the deterministic
actions stand — the explicit shell tells the operator what the
library's close paths only log.

**The operator verbs (NP slice B, ledger #104)** — `versions`, `redact`,
`revert` — join `maintain` on the operator side of the line: keyless
audit and remedy acts, never dispatch commands, never in
`ARGUMENT_KEYS` (a model emitting `command="redact"` through any
dispatch surface gets the corrective unknown-command answer, never a
real redaction). `versions PATH [--limit N]` lists a document's version
rows newest first — text output is the audit trail without content;
`--json` carries every row's full content, which is how point-in-time
reads are exposed (history reveals everything; `redact` is the only
eraser). Empty history is an answer (exit 0), not an error — it is a
query over history, while an unknown mount still fails correctively.
`redact PATH [--all]` clears one document's content everywhere; a mount
root redacts the whole scope only with the explicit `--all` — without
it, or with `--all` on a document path, the grammar refuses at exit 2
(a pure string check; nothing constructed). Scope-wide output names the
scope, because two mounts on one scope share the erasure. A read-only
mount refuses (`writable`); an edit-only mount allows it (a content
act, §8). `revert PATH --version N` is `revert_memory`'s shell face
(#101); its success envelope is the receipt's fields. Every verb's
`--json` prints its own envelope, never the six-command `ToolResult`
shape; a tier-1 failure under `--json` prints one minimal
`{"error", "hint"}` object on stdout — §14.1's one-JSON-object promise
stays literal — with the `error:`/`hint:` lines on stderr either way.
Grammar-tier guards are mandatory where a store method would raise a
bare `ValueError` (`--limit`/`--version` < 1): `run()` catches only
`MemoryStoreError`, so the guard is what keeps the CLI traceback-free.
None of the verbs emits a `memory_write` event (#99 — off-stream writes
stay silent). All three are keyless forever: no client factory, no
provider SDK.

### §14.3 The `cli` transport on the harness axis

`Transport.CLI` joins §13.12's axis: a cli cell's memory tool
(`evaluation/memory_cli.py`) converts each call's arguments to argv —
mirroring the engine's own `ARGUMENT_KEYS`, omitting `None` values so a
missing argument gets the grammar's exit-2 answer, resolving the
`file_text` alias at the boundary because the grammar has no alias
flag — and runs the real engine in-process: grammar, a fresh store
built from `--root` on every call (a real process's cold-store
property), dispatch, the `--json` envelope parsed back into a
`ToolResult`. `env={}` always: an ambient `NEOSIAN_POSTGRES_DSN` must
not reach a cell. The runner passes `memory_config=None` and the cli
tool via `extra_tools` — exactly one `memory` tool on the wire, the
same `build_memory_tool` definition, so the schema the model sees is
transport-invariant and the axis measures transports, never prompts
(#43's logic). The actor stays `eval:<scenario>:<session>`, riding
`--actor` verbatim through the argv boundary.

**Executed in-process, deliberately (#78).** What can drift between the
function tool and the shell — flag spelling, the argv mapping, the
envelope, the exit tiering — is crossed on every call; an OS fork adds
no memory semantics, costs ~10 interpreter starts per pack run in the
keyless gate, and turns tracebacks into exit-code archaeology. The
honest limit: the process boundary itself (console-script wiring,
`sys.argv` slicing, real pipes) is out of scope here and pinned once
per gate by the scripted keyless walkthrough, which drives all six
commands — and NP's redaction leg over the operator verbs — through
the literal `neosian` binary.

### §14.4 The docs door

An agent in a sandbox has the wheel, not a website. Docs that travel in
the package are version-true by construction — the stale-doc-site
failure mode is closed by shipping the prose, not by publishing it
faster.

`neosian docs [topic]`: no topic prints the curated listing (topic +
one-line summary) on stdout with the `neosian docs <topic>` hint on
stderr; a known topic prints the page body verbatim — no rich, no
wrapping, no colour: the bytes are markdown a model reads, and stdout
must survive a pipe; an unknown topic exits 2 with the topic list as
the hint. `--json` governs the executed tiers as everywhere else
(§14.1), so an unknown topic stays text on stderr even under `--json`.

The pages are §7 extended from prompts to documents — ECOSYSTEM §8's
"markdown + frontmatter" clause made real: five curated pages under
`assets/docs/` (`quickstart`, `memory`, `cli`, `mcp`, `topology`),
loaded by `shared/docs_assets.py` with the shared frontmatter parser.
`title` and `summary` are required keys; a module-local topic tuple is
simultaneously the curated reading order and the manifest, so a page
added without registering it — or registered without shipping — fails
at import instead of becoming invisible. Validation is fail-fast at
module import, as the prompt pack's is; because the CLI imports the
loader lazily, a unit test loads every page so a broken wheel is
caught by the gate, never by a user's `neosian docs` call.

The mandatory page is `topology`: NM's two axes — who runs neosian
code × where the bytes live — the four shapes, and §8's
one-writer-per-root rule (#74). It ships before the daemon exists
because the wrong mental model forms at first contact, not at
deployment; the page marks the daemon unshipped, and NM adds its
appliance quickstart there when it lands.

`llms.txt` is the outer door: the repo-root copy is what an agent
finds at the git URL, `assets/llms.txt` is what travels in the wheel,
and the two are byte-identical — pinned by a unit test rather than a
build-config force-include, with the install pin tied to
`__version__` (#79).

### §14.5 Self-setup, and the process boundary closed

`neosian mcp install --client <c> [--write]` prints the exact
registration by default and applies it only with `--write`. Print mode
puts the paste-able `mcpServers` JSON fragment — nothing else — on
stdout and the target path plus the `--write` hint on stderr, so
`> snippet.json` always yields a valid document (§14.1's split paying
rent). Routing is one literal first token: `install` is intercepted as
`argv[0]` before the server grammar parses, because that grammar stays
flat — subparsers would rename the documented
`python -m neosian.mcp --root … --scope …` invocation for no gain.

The registration is the *resolved* settings re-rendered, never a
transcription: the same `add_store_arguments(default_actor="mcp")`
parses the store flags and `format_mount` (#75) prints them back, so
`--scope` reaches the client's config as its canonical `--mount` token
and `--root` arrives absolute (a client spawns the server from an
arbitrary cwd). The `command` is the current interpreter's absolute
path + `-m neosian.mcp` (#81 — GUI clients inherit no shell PATH). A
DSN is never written into a client config — Postgres arrives through
the client's own environment, and the plan says so in a hint: #53's
rule one tier further out, where the file is *more* readable than
argv, not less.

Refuse, never create (#80): a missing client config directory is an
environment error (exit 1) naming the path — neosian does not `mkdir`
another program's config home, and an uninstalled client is not a
grammar mistake. An existing file is merged key-preserving (only the
`neosian-memory` entry is replaced); an unparseable one, or an
`mcpServers` that is not a JSON object, is refused rather than
rewritten. The per-client mapping is one table — config path, evidence
directory, servers key — three rows today (#81 fixes the claude-code
row); a client whose registration is a shell one-liner rather than a
JSON file would be a second render mode, not a row, and that cost is
stated so it is chosen deliberately.

The process boundary: §14.3's honest limit closes here. One keyless
walkthrough per gate drives the literal `neosian` binary through the
four doors — discover (`llms.txt`), learn (`neosian docs`), operate
(all six memory commands, a payload through a real stdin pipe, a
`--json` envelope parsed back, the exit-2 tiers, the DSN conflict)
and upgrade (`mcp install`, print mode writing nothing; the
missing-client refusal creating nothing) — at the interpreter-start
tax #78 sanctioned, with no skip path: if the console script stops
installing, the gate goes red.

## §15 Reflection (NR)

Session-boundary auto-memory — the missing link between the history
layer and the memory layer: a Conversation that never explicitly wrote
memory ends its session and the store holds the right facts. Appended
after §14 for the #63 reason. Rulings: ledger #85–#88.

**The engine** (`_foundation/conversation/reflection.py`, agent-free —
it joins the storage-seam import contract). One batched structured-output
call through the injected `acquire` lease (the distill idiom; the two
share `structured_call`): the system prompt is the shipped
`reflection.system` asset, the payload is the current memory — every
*writable* mount with its live document bodies, raw so an emitted
`old_str` matches stored content exactly; read-only mounts take no
operations and are not shown; edit-only mounts (NP slice B) *are*
shown, their headers annotated "update existing documents; never add
or remove one" — their documents stay editable, and a create or delete
the model proposes anyway fails correctively at the dispatcher and is
skipped — followed by the session transcript
(`render_turn`, verbatim). The model returns `ReflectionBatch`: a list
of operations over the deliberately tight command subset
`create`/`str_replace`/`delete` (`insert` and `rename` add nothing at a
boundary; the schema, not runtime enforcement, is the steer). Each
operation executes through the shared dispatcher
(`memory/dispatch.py`) — the same ladder as the four transports — with
`actor = conversation_id`, so every write lands as an audited version
row indistinguishable in mechanism from an in-session tool write.

**Dedup is evidence-based, not exhorted**: the payload *shows* the live
documents, and the prompt's rule is update-what-you-can-see. The
no-secrets rule rides the same prompt and is measured, not trusted (the
pack's `forbidden` pins and the unit-tier negatives).

**Failure is degrade-only.** A failed model call warns and returns an
empty result — the affected turns stay pending for a later retry; a
failed operation warns and is skipped while the rest land; reflection
never raises into a close (`aclose()` catches everything). A close
always closes.

**Trigger and default (ledger #85).** `Conversation.reflect()` mirrors
`compact()`: takes the send lock, always runs regardless of `enabled`,
covers exactly this instance's unreflected sends (tracked as pending
turns, cleared when a call lands — even with zero writes), and returns
`ReflectionResult(writes, usage, model)`. The `aclose()` rider is
default-on (`ReflectionConfig(enabled=True)`, resolved like
CompactionConfig; `model=None` = the agent's own model): a
memory-bearing close with pending sends reflects first, then releases
the pool, and returns the result — `aclose() -> ReflectionResult |
None`. The rider never *waits* on the send lock: held (an abandoned
stream) means reflection is skipped with a warning and the pool still
closes. A sendless instance closes silently. Per-request hosts — one
Conversation per HTTP request, the FastAPI example — set
`enabled=False` and call `reflect()` at their real session boundary.

**Receipts, no gate (ledger #86).** `ReflectionWrite(command, path,
version)` per landed write; the version rows behind them are the durable
audit trail and the undo substrate. Since NP, `version` comes from the
dispatcher's `MemoryWriteReceipt` (#98) — the post-hoc `_live_version`
re-read is gone; the field keeps its documented meaning (the live
version after; None once deleted) and `path` keeps the op's own
spelling. No approval callback (NT owns the interception seam); write
events stay off-stream here — a boundary write is not part of a turn
(§6, #99).

**The frozen index is untouched (ledger #87).** Reflection writes
surface in the next conversation's frozen index — the rule's own
design — and §9.5.10's compaction boundary remains the only legitimate
refresh point. Spend is visible on the result object only; nothing
folds into a send.

**Measured (ledger #88).** `MemorySession.reflect` on the harness runs
the same engine over the session transcript after the turn loop
(actor = the eval actor; acquire = `Agent._create_client`, so scripted
cells consume the script's next turn as the reflection response and
external cells make one real call). The shipped pack's
`reflection-close` scenario keeps turn expectations deliberately bare
and pins counts + content + forbidden only — a real model may
legitimately also write in-session, and in-session and boundary writes
produce different, equally correct version histories. The
discriminating negatives (a reflection duplicate, a reflected token)
live in the unit tier per the #67 idiom. `reflection.yaml` joins
`memory.yaml` and the pack behind the BASELINES.md fingerprint gate:
no reflection-prompt change without a recorded baseline re-run.

## §16 Maintenance (NG)

The gardener — memory that ages instead of rotting: an explicit,
operator-cadence consolidation pass over a store's writable mounts —
merge duplicates, prune stale, promote project→user, confirm-or-decay —
tractable in the file school precisely because memory is small
documents you can list and read whole. Rulings: ledger #90–#93.

**The engine** (`_foundation/memory/maintenance.py` — the memory layer,
inside the storage import contract, so provider clients arrive only
through the injected `acquire` lease). `run_maintenance(config, *,
acquire=None, model=None, actor=None, clock=None, min_age=7 days)` →
`MaintenanceResult(writes, usage, model)`, with
`MaintenanceWrite(command, path, version)` receipts (`path` is the
virtual tool path — a rename's destination; `version` the live version
after, None once deleted — populated from the dispatcher's
`MemoryWriteReceipt` since NP, #98). `acquire` and `model` come together
or not at all (`ConfigurationError` otherwise); a negative `min_age` is
refused.

**Stage 1 — deterministic, always, keyless (ledger #91).** Per writable
mount: redacted entries are skipped before anything else, then
documents whose content strips to empty are pruned and byte-identical
duplicate groups are merged — the keeper is the earliest `created_at`,
ties to the lexicographically first path; the rest are deleted. Exact
content equality needs no judgment, which is what makes this stage safe
to run without a model. Edit-only mounts are skipped whole (NP slice
B): every deterministic action is a delete, and their document set is
fixed.

**Stage 2 — the model pass, only when a model is given.** The payload
is every writable mount's live raw bodies (the §15 shape — raw so an
emitted `old_str` matches stored content exactly; read-only mounts take
no operations and are not shown; edit-only mounts are shown annotated
"update existing documents; never create, delete, or rename one";
redacted documents are named, never
read) annotated with the aging evidence the model rules on: version,
created/updated dates, and a `[fresh — protected from deletion]`
marker. One structured-output call (the shared `structured_call`,
promoted at NG from distill.py to `shared/structured.py` — compaction,
reflection and maintenance now share it) returns `MaintenanceBatch`:
ops over `create`/`str_replace`/`delete`/`rename` — `rename` joins the
§15 subset because promotion *is* a cross-mount rename (the composed,
non-atomic §8 move) — as per-command all-required shapes in a plain
anyOf union (the OpenAI strict-mode rule reflection.py pinned; a
keyless wire-schema test guards the gardener's union too).
Confirm-or-decay is realized as model judgment over that evidence — no
pass counters, no extra state; age alone is not decay, and the prompt's
bias is conservative: unsure means leave it.

**Protection is enforced in code, never exhorted (ledger #92).** The
age floor gates *deletion* in both stages — the deterministic deletes,
the model's `delete` — while `create`, `str_replace` and `rename` on
fresh documents stay legal (a merge's survivor may be fresh; a rename
preserves content). Redacted documents take no operation at all: a
redacted document reads back empty, so an unguarded empty-prune would
eat exactly the audit skeleton C3 promised to preserve. The edit-only
rule (NP slice B) is a third code-enforced protection: `_fixed_set`
refuses a model `delete` or a `rename` touching an edit-only mount on
*either side* with a named reason — the dispatcher would refuse anyway,
but the operator deserves the #92-style reason line, and the rename
destination is a check the single-path protection helper cannot see. A
path the
protection check cannot resolve falls through to the dispatcher's
corrective failure. Every mutation — both stages — runs through
`memory/dispatch.py` with the caller's actor, so each lands as an
audited version row (no new `MemoryAction`: the actor carries
provenance, the vocabulary stays closed).

**Failure is degrade-only; spend is visible.** A failed model call
warns and returns the deterministic result; a failed operation warns
and is skipped while the rest land. Usage and the API-reported model
ride the result — through `cost_micro_usd` at the shell — never folded
into anything else.

**Trigger — explicit only (ledger #90).** The public `run_maintenance`
and the `neosian memory maintain` verb (§14.2) are the only triggers:
no Conversation rider, opt-in or otherwise. Maintenance is scope-wide
work at operator cadence — a close-time rider would re-judge the whole
store on every close (churn and a second, store-sized model call),
reflection already owns the session boundary, and #87 means mid-session
writes only surface next conversation anyway. The CLI's model stage
rides an injected client factory (the storage contract denies this
package the router); the entry tiers supply a lazy router-backed one
that checks the provider key eagerly (`require_provider_key`, the #84
parity) so a missing key is loud at construction, and keyless commands
stay provider-SDK-free.

**Measured.** `maintenance.yaml` (the `system` prompt) sits in the
BASELINES fingerprint gate as its fourth file; slice B landed its first
measured cells — the shipped pack's seeded `maintenance` scenario
(§13.12: the `seed:` block plants the polluted store, the `maintain:`
step runs this engine, store truth pins the dedup, the prune, the
promotion, and the fresh document's survival). The NG done-when's first
half stays pinned keylessly in the unit tier: a deliberately polluted
store — dupes, stale, misfiled, empty — is measurably improved by one
scripted pass, with the protected documents (fresh, redacted,
read-only, edit-only) intact.
