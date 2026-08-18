# Neosian Roadmap — Memory & Conversation

> **▶ Current phase: N0 — Enablers** *(slice A ✅ 2026-08-19 at v0.55.0;
> slice B — event schema v2, ContextPolicy, eval keyless runs, CLI
> persist/replay — remains; see the split note under N0)*
>
> *(2026-08-18: NS done — the seams are frozen in code: µ$ money + token
> classes, machine error codes + SDK wrap, FakeProvider registry members +
> `neosian.fake`, prompts-as-data, the two register bugs fixed; `v0.54.0`
> cut — the kit's first vendoring point. Standing ruling: neosian's phases
> run to completion before the kit's P10 vendors from a `v<X.Y.Z>` release
> tag. Next: N0 → N1.)*
>
> The pointer above must equal the first phase heading without ✅ — if they
> disagree, say so and trust the checkboxes. Companion to [VISION.md](VISION.md)
> (*why and what*) and [DESIGN.md](DESIGN.md) (*how*); this file says *in what
> order* and holds cross-session state. Version numbers are cadence markers,
> not dates. Each phase ends dogfooded before the next begins.

## Session protocol

1. **Start:** read this file fully, then the [DESIGN.md](DESIGN.md) sections
   the current phase names, then [CLAUDE.md](CLAUDE.md). The Decided
   constraints below and [ECOSYSTEM.md](ECOSYSTEM.md) are settled — never
   relitigated inside a phase.
2. **One phase per session.** The user reviews between phases; split a phase
   rather than overrun it.
3. **During:** if reality contradicts DESIGN.md, the same session either bends
   the code or adds a §12 ledger entry — never a silent divergence.
4. **End:** tick the phase's boxes, carry unfinished tasks forward explicitly,
   append a session-log line, mark the heading ✅ with the date, move the
   ▶ pointer, commit (`<ID>: <what became true>`), tag `<id>-done` (annotated),
   stop for review.

## Phase ids, tags, versions

- Phase ids: `NH`, `NS`, `N0`–`N4`. Commit subjects `<ID>: <what became true>`
  (≤ 72 chars); `meta:` for repo housekeeping. The `v:X.Y.Z. …` subject style
  is retired (DESIGN §12 #7).
- Progress axis: annotated **`<id>-done`** tags at each phase close.
- Release axis: annotated **`v<X.Y.Z>`** tags — what hosts vendor
  (ECOSYSTEM §11). The version literal lives once in
  `pyproject [project].version` (from NH); bump on public-surface change at
  phase close, not per commit. Push with `--follow-tags`.

## The destination

An opt-in memory and conversation layer around the stateless agent core:
`uv add neosian` gives Claude-Code-grade, agent-curated memory on any of the
four providers, against a plain file directory or your own Postgres —
history persistence, cross-session memory, and compaction with configurable
defaults, in three lines:

```python
store = PostgresStore(dsn)          # or FileStore(path)
convo = Conversation(agent, store=store,
                     conversation_id="thread-829",
                     memory_scope="user:1234")
resp = await convo.send("Where did we leave off?")
```

---

## Decided constraints (carried from VISION.md + design discussions)

These are settled and shape every phase:

- **Stateless agentic core.** `Agent` stores nothing between calls;
  `Conversation` and memory are opt-in layers around it, never state
  smuggled in.
- **Tools-only recall** — no prompt injection of memory bodies (cache
  preservation, pay-per-use, audit trail via `tool_calls_made`).
- **Anthropic's memory command vocabulary as the universal interface**:
  `view`, `create`, `str_replace`, `insert`, `delete`, `rename` over a
  virtual path space — implemented as plain function tools on all four
  providers first; native `memory_20250818` wiring on Anthropic is a later
  optimization that rides the models' trained memory behavior.
- **File-school storage**: small documents + frontmatter + an index. No
  embeddings, no vector stores — wrong regime for agent working memory.
- **Frozen index per conversation**: the memory index is injected once at
  conversation start (cache-safe); writes land immediately but surface in
  the prefix at the next conversation — or at a compaction boundary, which
  already invalidates the cache and is therefore the free refresh moment.
- **Two-key model**: `conversation_id` keys history (per thread);
  memory is keyed by **mounts** — a list of
  `(scope, mount_path, read_only, description)`. Scopes are opaque strings
  (`user:123`, `user:123/proj:erp`, `tenant:acme/kb`); hierarchy is a
  naming convention plus explicit mounting, never inheritance logic in the
  storage layer. Canonical configuration mirrors Claude Code: a user mount
  plus a project mount. `memory=True` requires an explicit scope — no
  silent isolation decisions.
- **Versioning + redaction in the storage ABC from day one.** Every
  mutation appends a version row (`created`/`modified`/`deleted`, path,
  content, actor = the writing `conversation_id`, timestamp); redaction
  clears content while preserving the audit skeleton. Version rows carry
  **full content** — memories are KB-scale, so point-in-time reads and
  rollback come essentially free. Cheap now, painful to retrofit.
- **Substrates**: FileStore (markdown + frontmatter for memory, JSONL for
  turns and versions) and PostgresStore. SQLite deferred — its niche
  (embedded desktop apps) is not a v1 audience, and the flat, path-free ABC
  makes adding it later trivial.
- **Compaction is neosian-built and structured — log-projection, not
  blind summarization.** History is an append-only log; compaction changes
  what is *in context*, never what is *stored*. Aged turns are projected
  to typed one-line log entries carrying a turn-ref, and a built-in tool
  re-hydrates any entry verbatim on demand — compaction is paging, not
  deletion. Runs on the agent's own model by default, configurable at
  agent creation. Lives in the Conversation layer, triggers off
  `ModelSpec.context_window`. Anthropic server-side compaction becomes an
  opt-in optimization where available, never the foundation. (Design
  direction sketched under N2 — refine in a dedicated discussion before
  implementation.)
- **The seam-level constraints are normative in [ECOSYSTEM.md](ECOSYSTEM.md)**
  (scope grammar, token classes, integer µ$, event vocabulary, error codes,
  FakeProvider, prompts-as-data, UTC, ABC-is-the-contract, vendoring);
  rationale lives in [DESIGN.md](DESIGN.md). Changing one is a two-repo move
  (ECOSYSTEM §12).

---

## NH — The house (v0.53) ✅ 2026-08-18

DESIGN: §10, §11, §12.

The kit-grade development-to-ship systems, adopted wholesale. No public-API
changes in this phase.

- [x] The four constitution docs cross-link and agree (ECOSYSTEM, DESIGN,
      CLAUDE, SERVICES — written 2026-08-18; NH verified and fixed drift:
      Makefile paragraph, weekly cadence, `_key_or_skip` name, cross-links)
- [x] `.claude/` tracked (only `settings.local.json` ignored); `/phase` and
      `/ship` rewritten against this file's protocol *(done 2026-08-18;
      verified against the Makefile — pre-Makefile fallback removed)*
- [x] `Makefile` per DESIGN §11 (help idiom; lint / format / typecheck / test /
      test-external / size / release / phase-tag; dirty-tree + required-arg
      guards)
- [x] Version flip: `[project].version` literal, `__version__` via
      `importlib.metadata`, `[tool.hatch.version]` deleted,
      `tests/unit/test_version.py` pins the derivation *(SPDX `license`
      field added alongside)*
- [x] Test-tier rename: real-API "integration" → `external_<provider>`
      markers; addopts exclude external by default; auto-mark by path in the
      root conftest; per-test skip helpers (never module-level); falsiness
      key checks *(suites restructured by provider dir; the groq
      agent/session suites gained the skip guard they were missing)*
- [x] `scripts/external_env.py` (value-blind cred injection, per-suite key
      allowlist, prints names only) + `scripts/check_file_size.py` (warn 300 /
      fail 500; the allowlist holds **five** files ≥ 500, each with a
      reason — playground, types, anthropic, exceptions join `agent/base.py`)
- [x] import-linter contracts in pyproject (DESIGN §1) wired into `make lint`
      *(first run caught a real break: `evaluation/runner` imported `_cli` —
      loader moved to `_foundation/agent/loader.py`, CLI-config credential
      loading hoisted into the CLI eval command)*
- [x] CI reshape per DESIGN §11: lint job · test matrix 3.12/3.13/3.14 with
      **no secrets** · external-provider jobs (schedule/dispatch,
      empty-string self-skip); event-keyed concurrency; pinned ubuntu-24.04
- [x] LICENSE = Apache-2.0 committed *(done 2026-08-18)*

**Done when:** `make lint typecheck test` is green with zero API keys set; CI
is green on 3.12, 3.13 and 3.14; the docs agree with the tree.

## NS — The seams freeze (v0.54) ✅ 2026-08-18

DESIGN: §4, §5, §2, §7 + ECOSYSTEM. Breaking changes batched into one release;
order within the phase: §4 (vocabulary) → §5 (errors) → FakeProvider → §7
(prompts-as-data).

- [x] Token-class rename (`cache_read_tokens`/`cache_write_tokens`) +
      `ModelPricing` to int µ$/MTok (kit field order; no-rounding test) +
      `cost_micro_usd()` ceiling division + `format_micro_usd` +
      `PRICES_FINGERPRINT` gate; `Usage.cost()` float dropped;
      `Usage`/`ModelPricing` frozen+slots; `ModelUsage` type *(golden rate
      card + ceiling vectors in tests/unit/shared/test_pricing.py; SSE usage
      payload keys renamed with the vocabulary — deliberate batched break)*
- [x] Error codes per DESIGN §5: `NeosianError` base with
      `code`/`retryable`/`details`, the full append-only table,
      `wrap_provider_error` at all four client boundaries
      (`raise … from exc`), `ContextWindowExceededError`,
      `ModelFailedError.cause_code`/`provider_status`, `ERROR_CODES` registry
      + `python -m neosian.schemas errors`; exports updated *(47 codes, all
      pinned; wrap gained keyword-only `model=` — DESIGN §5; Groq-413 gap
      recorded as ledger #13)*
- [x] FakeProvider per DESIGN §2: `Provider.FAKE`, `Model.FAKE` /
      `FAKE_SMALL` / `FAKE_REASONING` as real registry members;
      `neosian.fake` public module (lazy-imported, root `__all__` untouched);
      `FakeScript`/`FakeTurn` determinism rules; router always includes FAKE
      (keyless boot); `AgentConfig.client_factory` seam *(media/reasoning
      capability split across the three fakes; keyless agent end-to-end
      suite incl. tool loop, fallback, sessions; `FakeScriptExhaustedError`
      appended to the §5 table)*
- [x] Prompts-as-data extraction (DESIGN §7): guardrail classifier prompt +
      category template + CommonPolicies content + builtin tool descriptions
      → `assets/` YAML, loaded/validated at import, overridable *(golden
      tests pin byte-identical assembly against the v0.53 in-Python strings)*
- [x] In-blast-radius bug fixes: `_validate_run` extracted and shared by
      `Agent.run` + `AgentSession.run` (the session skipped all guards —
      three, not four; DESIGN §3 wording fixed); `_attach_input_guard_results`
      → `dataclasses.replace` (fixes the dropped `model=`) *(parametrized
      suite runs identical scenarios over both entry points)*
- [x] `__all__` + `test_init` pin updated per contract; ECOSYSTEM already
      carries the v1-frozen header (verified, no edit);
      **first release tag `v0.54.0`** cut with `make release`

**Done when:** every vocabulary item frozen in ECOSYSTEM is exercised by a
test; the annotated `v0.54.0` tag exists (the kit's first vendoring point).

## N0 — Enablers (v0.55–0.56)

DESIGN: §3, §6.

The prerequisites identified in VISION.md, plus the event schema v2 that
depends on them. Required regardless of everything below.

| Deliverable | Why |
|---|---|
| Session-twin refactor (`client_factory` collapsing the `_with_session` duplicates in `agent/base.py`; the file leaves the size-gate allowlist) | Every later insertion point is currently doubled; halves the diff of all memory work |
| Turn capture — `AgentResponse.turn_messages` (contract in DESIGN §3: tuple of provider-order messages; `turn_messages[-1] is response.message`; input + turn_messages replays as valid history) | Callers cannot faithfully persist a turn today; `Conversation` is impossible without it |
| `AgentHooks` (`on_turn` / `on_llm_call` / `on_tool` / `on_fallback`; one frozen event dataclass per hook; sync-or-async; swallow-unless-strict; `LlmCallEvent.model` is the API-reported string) | Memory persistence in streaming mode needs a turn hook; replaces the eval runner's log-scraping; `on_llm_call` maps 1:1 onto a host's metering `record()` |
| `ContextPolicy` — token counting + window fitting; proactive `ContextWindowExceededError`; no-fallback-to-smaller-window rule | Makes `ModelSpec.context_window` live; the compaction trigger later |
| Event schema v2 (DESIGN §6): typed frozen events, `run(stream=True) -> AsyncIterator[AgentEvent]`, `StreamChunk.model`, `usage_by_model` on terminal events, `sse_stream`, `event_schemas()` export; old SSE surface removed | Consumes the hooks' resolved model + per-model usage; the host-facing stream contract |
| `AgentResponse` frozen+slots, tuple fields, `usage_by_model`; usage-smuggling private attrs retired (public `LLMError.usage`/`usage_by_model`) | One value object, no field-dropping rebuilds |
| Eval harness moves onto hooks + FakeProvider (fallback detection via `on_fallback`, not log substrings; `FakeClient` conformance for both provider stream shapes) | Deletes the log-scraper; keyless eval runs |

**Done when:** the CLI can faithfully persist and replay a full multi-turn
session including intermediate tool messages.

**Split (2026-08-19): slice A shipped at v0.55.0** — session-twin collapse
via frozen `RunContext` + `agent/base.py` split into nine sibling modules
(2467 → 329 lines, allowlist entry removed); `AgentResponse.turn_messages`
per the §3 contract (with the found-bug #5 per-attempt snapshot fix);
`AgentHooks` + the four frozen events wired at every insertion point,
exported (+5 `__all__` names); `AgentResponse` frozen+slots, tuple fields,
`usage_by_model`; usage smuggle retired → public
`LLMError.usage`/`.usage_by_model` on both paths (blocking hole closed);
`StreamChunk.model` in all five clients; eval runner's fallback detection
moved onto `on_fallback` (the file split would have silently killed the
log scraper). **Slice B carries forward:** event schema v2 (`run(stream=
True) -> AsyncIterator[AgentEvent]`, `sse_stream`, `event_schemas()`, old
SSE surface removed — wire payloads deliberately untouched in slice A),
`ContextPolicy`, eval harness keyless FakeProvider runs, and the
CLI-persist/replay done-when.

## N1 — Memory core (v0.57–0.59)

DESIGN: §8, §7.

- Opens with the `MemoryStore` ABC exactly per DESIGN §8 (the seven
  constraints), the scope validator (`parse_scope`, ECOSYSTEM §2), and the
  shipped `MemoryStoreContract` conformance kit.
- `FileStore` memory implementation (markdown + frontmatter; JSONL version
  sidecar).
- Memory documents carry the **format-version marker** in frontmatter from
  the first release (`neosian_format: 1`) — the escape hatch for evolving the
  schema without breaking existing stores.
- The six-command tool set as provider-agnostic function tools, operating on
  one virtual path space (mounts appear as top-level directories);
  `str_replace`/`insert` are tool-layer compositions (the store stays at 7
  methods).
- **The prompt pack** — memory is tool + prompt + index, three pieces:
  write discipline (check before create, update not duplicate, delete what
  proved wrong) and mount routing (user-durable facts up, project facts
  local), modeled on Claude Code's. Ships as data (DESIGN §7), never prose
  in Python.
- Index generation + injection (frozen per conversation).

**Done when:** an agent in the CLI demonstrably accumulates memory in one
session and uses it in the next.

## N2 — Conversation layer (v0.60–0.62)

DESIGN: §9 (written by this phase's design discussion), §3.

- `Conversation`: append-only history, `send()` with streaming parity,
  `resume(conversation_id)`, configurable defaults.
- Two-key + mounts API surface (single-scope sugar:
  `memory_scope="user:123"` builds a one-mount list).
- Compaction v1 — the **log-projection** design (refine in a design
  discussion before implementing):
  - The context window renders a *view* of the append-only history:
    recent turns verbatim (hot), aged turns as typed one-line log entries
    (warm), oldest spans folded into epoch summaries (cold).
  - Log entries are computed per turn, once, and checkpointed — never
    re-summarize the whole transcript.
  - Deterministic projection first (tool calls/results have known shape:
    name + args + head/tail of result — free, no model call); model
    distillation only for long prose, batched at compaction checkpoints
    via structured output (k turns in → k log lines out, one call).
  - Every entry carries its turn-ref; a built-in `recall_turn` tool
    re-hydrates the verbatim turn — compaction is paging, not deletion.
  - User turns are compacted least aggressively; stated constraints and
    decisions survive verbatim.
  - Memory-index refresh happens at the compaction boundary (the free
    cache moment).
  - Optimize for model-native readability, not micro-token tricks: role
    labels stay full words (`USER`, `TOOL`, `AGENT` are single tokens in
    modern vocabularies, model-native, and human-greppable; four provider
    tokenizers make micro-optimization fragile anyway). Token discipline
    comes from digest length and epoch folding, measured by the N4 harness.
  - Distillation calls report through the cost accounting
    (`cost_micro_usd`) — compaction spend is visible, never hidden.
- **CLI migrates onto `Conversation` + `FileStore`** — the dogfood that
  also fixes its lost-tool-history bug.

Explicitly **not** in scope: branching/forking, history editing (ruled out
in VISION.md), transport, auth.

**Done when:** the three-line quickstart — construct store, construct
conversation, `send()` — yields a stateful, memory-bearing agent.

## N3 — PostgresStore (v0.63–0.64)

DESIGN: §8.

- Schema: `conversations`, `turns`, `memories`, `memory_versions`; scope
  column on every memory row; row-level-security-friendly layout.
- Optimistic concurrency for multi-worker web deployments
  (`supports_optimistic_concurrency = True` — the thing files cannot do and
  the reason this substrate exists).
- FTS escape hatch (`tsvector`) behind the same recall interface — dormant
  until a tenant's memory outgrows index-scan-plus-grep.
- Migration story (SQL files or alembic — decide small). Reference
  implementation for **standalone** deployments; a host embedding neosian
  implements its own store against the ABC + `MemoryStoreContract`
  (ECOSYSTEM §10).

**Done when:** two concurrent web workers on one conversation behave
correctly; an `examples/` FastAPI chatbot demonstrates the multi-tenant
integration end to end — including the relay pattern of DESIGN §6 (typed
events → SSE, host-owned keepalive and error frames).

## N4 — Completeness (v0.65 → 1.0)

DESIGN: §2, §6, §10.

- Native `memory_20250818` tool type on Anthropic behind a flag (same
  store, provider-native surface; rides the trained behavior).
- Anthropic server-side compaction as an opt-in where available.
- **MCP memory server packaging** — neosian memory usable from Claude Code,
  Claude Desktop, Cursor, any MCP client, backed by the same stores.
  Ships as an optional extra — `uv add "neosian[mcp]"`, module
  `neosian.mcp` — one repo, one release train.
- Memory eval harness built on the existing `evaluation/` module — there is
  no public benchmark for the agent-memory regime (LoCoMo measures the
  personalization regime), so we measure ourselves: write discipline,
  recall-in-next-session, dedup behavior — per provider, FakeProvider
  baselines first.
- Docs and quickstarts. **1.0 = API stability promise** for `Agent`,
  `Conversation`, `MemoryStore` — and the ECOSYSTEM seams move from
  append-only-by-convention to SemVer-guaranteed.

---

## Risks

- **Trained-behavior asymmetry.** Anthropic models are post-trained on the
  memory command set; other providers' models less so. The prompt pack
  carries more weight off-Anthropic — the N4 eval harness measures this per
  provider instead of assuming.
- **Conversation-layer scope creep.** `Conversation` is where frameworks
  bloat. The not-in-scope list in N2 is a commitment, not a suggestion;
  anything beyond append-only + resume + memory + compaction needs a
  vision-level discussion first.
- **Solo-maintainer bandwidth.** Defense: every phase is small, shippable,
  and independently useful — the library is already better off if the
  roadmap stops after any phase.

---

## Session log

- 2026-08-18 | meta | **The ecosystem contract + the constitution.** Joint
  design session with neosae-kit (its ledger #205, DESIGN §5.7): ECOSYSTEM.md
  written as the canonical frozen-seam contract; DESIGN.md created (12
  sections, seeded ledger); this file reshaped to the kit's form (pointer
  block, session protocol, NH/NS phases inserted before the memory arc,
  Done-when lines, this log); CLAUDE.md, SERVICES.md, LICENSE (Apache-2.0)
  created; `.claude/commands` now tracked and rewritten. Rulings: integer µ$
  canonical (float `cost()` dies — zero call sites); kit token-class names
  adopted; ABC-is-the-contract (hosts implement their own store; the kit never
  uses PostgresStore); machine error codes append-only under family prefixes;
  events become typed frozen dataclasses with `run(stream=True) →
  AsyncIterator[AgentEvent]`; FakeProvider as real registry members; py-floor
  stays 3.12 with a 3.12–3.14 CI matrix. Audit found three real defects
  (session guard skip; `model=` dropped when input guardrails pass; per-model
  usage impossible under fallback) — registered in DESIGN §3, scheduled NS/N0.
  Docs only; no code touched.
- 2026-08-18 | NH | **The house.** Makefile (§11 targets + guards); version
  flip (`[project].version` = 0.53.0, `__version__` via importlib.metadata,
  pinned by `test_version.py`); tier rename to
  `tests/external/{groq,cerebras,anthropic,cross}` with path auto-marking,
  `-m "not external" --strict-markers` addopts, consolidated `_key_or_skip`
  (the groq agent/session suites had **no** skip mechanism — fixed);
  `scripts/{check_file_size,external_env}.py` (size allowlist: five files
  ≥ 500, each with a reason); import-linter wired — its first run caught
  `evaluation/runner → _cli` (loader moved to `_foundation/agent/loader.py`,
  credential loading hoisted to the CLI eval command). `mypy --strict
  neosian tests` fixed for real: 207 errors → 0 (Any-typed `_sdk()` mock
  accessors, NewType wraps, `_python_type_to_json_schema` annotation widened
  to its documented contract). CI: lint · keyless 3.12–3.14 matrix ·
  external×4 with per-provider secret isolation; event-keyed concurrency;
  ubuntu-24.04. Docs drift fixed (CLAUDE/SERVICES/README/commands).
  Carried forward: matrix-green confirmation happens at /ship.
- 2026-08-18 | NS | **The seams freeze.** §4: token classes renamed
  (`cache_read/write_tokens`), `ModelPricing` → int µ$/MTok kit order (12
  literals converted exactly; no-rounding golden card), `cost_micro_usd`
  ceiling division + `format_micro_usd` + `MICRO_PER_USD` +
  `PRICES_FINGERPRINT` gate, float `cost()` dead, `Usage`/`ModelPricing`
  frozen+slots, `ModelUsage` added; SSE usage keys follow the vocabulary.
  §5: every exception carries `code`/`retryable`/`details` (47 codes, table
  pinned append-only), `wrap_provider_error` wraps all eight complete/stream
  bodies (`raise … from exc`; streams' `except Exception` spares
  GeneratorExit/CancelledError; anthropic's `async with` inside the try),
  `ContextWindowExceededError` (wrap gained keyword-only `model=`),
  `ProviderError(provider, message, *, status, retryable, request_id)`,
  `cause_code`/`provider_status` on terminal errors, `ERROR_CODES` +
  `python -m neosian.schemas errors`. FakeProvider: three registry models
  with a deliberate capability split, `neosian.fake` (FakeClient/FakeScript/
  FakeTurn/FakeCall, both stream shapes, failure injection through the
  wrap), router always offers FAKE, `AgentConfig.client_factory` honored at
  every creation site via one `_create_client` helper (twins intact for N0).
  §7: guardrail classifier + category + six policies + six tool descriptions
  → `assets/prompts/*.yaml` ({{var}} interpolation), loaded fail-fast at
  import; golden tests pin byte-identical assembly. Bugs: `_validate_run`
  shared by both entry points (three guards; DESIGN §3 said four — wording
  fixed); `_attach_input_guard_results` → `dataclasses.replace` (model= no
  longer dropped). Ledger #13 (Groq 413) and #14 (fakes visible in
  INVALID_MODEL) added. 935 unit tests, zero keys; v0.54.0 — the kit's
  first vendoring point.
- 2026-08-19 | N0 (slice A) | **The agent core reshaped.** Twins collapsed:
  one `Agent._dispatch` funnel + frozen `RunContext` (`acquire` =
  client_factory seam, sticky state, hooks); base.py 2467 → 329 lines
  across nine sibling modules (blocking, loop, stream_run, stream_loop,
  stream_final, guards, fallback, tool_exec, emit); allowlist entry
  removed. Turn capture: per-attempt `Attempt` (message snapshot + usage
  ledger keyed by API-reported model, inherited across fallback attempts)
  → `AgentResponse.turn_messages` (`[-1] is message`, replayable), fixing
  found-bug #5 (fallback saw the failed main's mutated history — register
  entry added). `AgentHooks` (on_turn/on_llm_call/on_tool/on_fallback,
  frozen events, sync-or-async, swallow-unless-strict, awaited inline for
  sequence determinism) wired at every site incl. streamed terminals;
  +5 `__all__` names. `AgentResponse` frozen+slots+tuples+`usage_by_model`;
  smuggle (`_neosian_stream_usage`) deleted → public `LLMError.usage`/
  `.usage_by_model` on both paths (blocking previously raised usage-less);
  blocked responses keep billed usage. `StreamChunk.model` populated by all
  five clients (anthropic: hoisted from message_start). Eval runner's
  `_FallbackDetector` log scraper → `on_fallback` recorder (+ runner's
  first tests, keyless). SSE wire payloads deliberately byte-stable — the
  v2 break stays batched in slice B. 975 unit tests, zero keys; v0.55.0.
  Carried forward: slice B (events v2, ContextPolicy, keyless eval runs,
  CLI persist/replay).
