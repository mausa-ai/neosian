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
│                         #   (+ memory/, N1; + conversation/, N2)
├── _cli/                 # playground, eval CLI, config
└── assets/               # data files: prompts (YAML), ascii art
```

Import-linter contracts (inline in pyproject, kit idiom — the matrix is spelled
out, `forbidden` type only):

- `_foundation ↛ _cli` — the library never imports its CLI.
- `_foundation.memory` ↛ provider internals (`_foundation.llm.<provider>`
  modules) — memory speaks only to ABCs.
- `_foundation.conversation` ↛ provider internals, with
  `allow_indirect_imports` (Conversation drives an `Agent`, which owns the
  router — ledger #26); and the conversation *storage-seam* modules (`base`,
  `types`, `ids`, `file_turns`, `testing`) ↛ `_foundation.agent`.
- The facade (`neosian/__init__.py`, `neosian/fake.py`) only re-exports; no
  logic lives there.

The public API is pinned: `tests/unit/test_init.py::test_all_list_matches_exports`
makes every `__all__` change a deliberate, reviewed diff.

## §2 Providers & the router

Four adapters (groq, openai, anthropic, cerebras) + FakeProvider (§C5 below,
NS) behind `BaseLLMClient`. Capabilities live in `ModelSpec` and describe
*neosian's converter*, not raw provider ability. Fallback is capability-aware
and sticky within a session; SDK-native retries stay at the client layer.
Provider SDK exceptions never escape neosian — `wrap_provider_error` (§5)
classifies them at the client boundary.

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
rides `on_fallback` instead of scraping logs. `Agent` exposes read-only
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
| EvalError family | `eval_error`, `eval_config_not_found`, `eval_config_invalid_yaml`, `eval_config_missing_key`, `eval_prompt_not_found`, `eval_case_invalid`, `eval_run_failed` | no |
| Memory family (N1) | `memory_error`, `memory_document_not_found`, `memory_scope_invalid`, `memory_path_invalid`, `memory_conflict`, `memory_format_unsupported`, `memory_read_only_mount` | no |
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
              | tool_progress | blocked | done | error
```

- `ReadyEvent(protocol, requested_model, provider)`.
- `ToolProgressEvent(tool_call_id, elapsed_ms: int)` — renamed from
  `heartbeat`: it means "this tool is still running", which no keepalive
  means. Keepalive is the host's SSE comment on the host's timer (only the
  host knows its proxy's idle timeout); neosian emits none.
- Terminal events (`DoneEvent`, `ErrorEvent`, `BlockedEvent`) carry `usage`
  (sum) + `usage_by_model: tuple[ModelUsage, ...]`.
- `DoneEvent.model` is the **API-reported** string of the final completion —
  unified with `AgentResponse.model` (enabler: `StreamChunk.model`, populated
  by all four clients from their first chunk / `message_start`).
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

Wire form: `event: <name>\ndata: <compact single-line JSON object>\n\n` with
`ensure_ascii=True` (escaped newlines — no model output can break framing);
byte-compatible with the kit's `_frame` and its `parseFrame`. No `id:` field —
resume is host territory. `sse_stream(events)` is the verbatim-relay adapter.
`EventSequencer.stamp()` returns `dataclasses.replace(event, sequence=n)`,
starting at 1 (the frontend owns 0). Schema export: `event_schemas()` +
`python -m neosian.schemas events [--out DIR]` — one schema per event plus a
`oneOf` root discriminated on `event`; hosts codegen their typed SSE seam from
it (the kit's OpenAPI deliberately excludes streaming shapes).

Removed with v2: `SSEEventType`, `SSEEvent`, `SSEEventEmitter`,
`stream_to_sse`, all eight `*_event()` builders — the dataclasses are the
constructors.

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
`actor` is the writing `conversation_id`, opaque. `expected_version=` raises
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

**The tool layer (N1 slice B; C7 made concrete).** `Mount(scope,
mount_path, read_only, description)` + `MemoryConfig(store, mounts)` in
`memory/mounts.py`; ≥ 1 mount, unique mount paths — "memory needs an
explicit scope" is structural. One `memory` function tool (ledger #19),
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
Read-only enforcement lives here, never the store: `writable()` is the
library's only `MemoryReadOnlyMountError` raise site. Every
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
1024-char OpenAI-compatible cap.

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
tables), delete/redact (no roadmap requirement; retrofit is cheap while the
seam is un-frozen — §9.10), per-turn usage/model/cost columns (§9.3), and any
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

**§9.9 Public surface & the codec ruling.** `message_to_json` /
`message_from_json` become public API: a host implementing
`ConversationStore` must encode messages with the same codec the library
reads back — leaving it private forces every host to hand-roll half a codec,
the exact drift CS5 forbids (ledger #22). Root `__all__` gains the
conversation block + the two codec names; `neosian.conversation` (+
`.testing`) mirrors `neosian.memory` (lazy, re-export only). `FileStore` is
re-exported from both facades — it implements both seams; `ProjectionKind`
and `ConversationId` stay facade-only, like `MemoryAction`.

**§9.10 Deferred ECOSYSTEM amendment.** ECOSYSTEM §10 names only
`MemoryStore` and §6's prefix set is closed — `ConversationStore` belongs in
both, but that is a two-repo move (ECOSYSTEM §12) this session cannot make.
Until the session-pair happens: **§9 is the contract of record, the seam is
not frozen for hosts** (the ABC docstring says so), and conversation codes
live under `agent_` (ledger #24). Amendment payload, recorded for that
session: ECOSYSTEM §10 gains `ConversationStore` + `ConversationStoreContract`;
§6 gains (or deliberately declines) a `conversation_` prefix; §12 log gains a
row.

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
| `test-external` | `provider=<groq\|openai\|anthropic\|cerebras>` required-arg guard; `file=…` routes through `scripts/external_env.py` |
| `size` | the 300/500 file-size gate |
| `release` | refuses a dirty tree; requires `v=X.Y.Z` matching pyproject; cuts annotated `v$(v)` |
| `phase-tag` | refuses a dirty tree; cuts annotated `<id>-done` |

**CI** (`.github/workflows/ci.yml`, reshaped in NH): triggers push-master /
PR / dispatch / weekly schedule; **event-keyed concurrency group** (a master
push must not cancel the scheduled run); runners pinned `ubuntu-24.04`;
`setup-uv` with cache. Jobs: `lint` (lint + typecheck + size) · `test`
(matrix **3.12 / 3.13 / 3.14**, fail-fast false, **no secrets** — that absence
is the keyless-boot assertion) · `external-<provider>` × 4 (schedule/dispatch
only; secret as env; suites self-skip on empty string).

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
| 13 | Groq reports "Request too large" with HTTP 413 | **Context-overflow classifier keys on 400 only**, as specced; 413 widening deferred | Widening blind risks misclassifying non-context 413s; append the status when a real one is observed |
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
