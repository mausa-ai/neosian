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
│                         #   guardrails/ blackboard/ evaluation/ (+ memory/, N1)
├── _cli/                 # playground, eval CLI, config
└── assets/               # data files: prompts (YAML), ascii art
```

Import-linter contracts (inline in pyproject, kit idiom — the matrix is spelled
out, `forbidden` type only):

- `_foundation ↛ _cli` — the library never imports its CLI.
- `_foundation.memory` / the Conversation layer ↛ provider internals
  (`_foundation.llm.<provider>` modules) — memory speaks only to ABCs.
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
yields a response), the fallback switch sites + sticky retry-main
(`on_fallback`). Hooks await inline — sequences are deterministic; the
eval runner's fallback detection rides `on_fallback` instead of scraping
logs.

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

The memory base class is **`MemoryStoreError`** — never `MemoryError`, which
shadows a Python builtin in `__all__`.

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

## §9 Conversation & compaction **(N2)**

The log-projection design is sketched under ROADMAP N2 and gets a dedicated
design discussion before implementation; the decisions land here as §9 then.

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
