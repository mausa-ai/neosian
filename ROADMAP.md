# Neosian Roadmap — Memory & Conversation

> **▶ Current phase: N4 — Completeness**
>
> *(2026-08-20: N4 slices A+B+C shipped — v0.65.0 the native
> `memory_20250818` flag (ledger #41–#44), v0.66.0 server-side
> compaction pass-through (ledger #45–#49), v0.67.0 the MCP memory
> server (`neosian[mcp]` extra, `python -m neosian.mcp`, the one
> `ToolDefinition` served verbatim over the shared `memory/dispatch.py`
> ladder; ledger #50–#53). Remaining in N4: the memory eval harness,
> docs/1.0. Standing ruling: neosian's phases run to completion before
> the kit's P10 vendors from a `v<X.Y.Z>` release tag. Still deferred
> per §9.10: the ECOSYSTEM amendment naming `ConversationStore` — a
> two-repo move for a future session-pair. CI is green: after the
> billing fix (2026-08-20), run 32407460787 on the v0.67.0 head passed
> whole — lint, the 3.12–3.14 keyless matrix, and the postgres job's
> first-ever execution — confirming v0.54.0 onward at once.)*
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

## N0 — Enablers (v0.55–0.56) ✅ 2026-08-19

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
log scraper). **Slice B shipped at v0.56.0** (2026-08-19): event schema
v2 per §6 (`run(stream=True) -> AsyncIterator[AgentEvent]`, `sse_stream`,
`event_schemas()` + the schemas CLI, old SSE surface deleted); default-on
`ContextPolicy` + the no-smaller-window rule (ledger #16/#17); eval
`script:` cases run keylessly via injected FakeClient; the CLI persists
and replays full sessions with tool messages via the new message codec —
the phase done-when.

## N1 — Memory core (v0.57–0.58) ✅ 2026-08-19

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

**Split (2026-08-19): slice A shipped at v0.57.0** — the storage seam:
`MemoryStore` ABC exactly per §8 (the seven constraints; the semantic
rulings — version numbering across delete/re-create, rename-conflict,
newest-first `versions()`, per-state `redacted`, best-effort
`expected_version` — written into DESIGN §8); scope grammar
(`parse_scope`, `SCOPE_PATTERN` anchored `\A…\Z`, `scope_directory`
storage encoding pinned to scope.py/file.py by a grep test) + document-path
grammar; the seven `memory_*` error codes (table whole,
`MemoryReadOnlyMountError` waits for the tool layer); `FileStore` —
exact envelope codec (byte-exact round-trip incl. content that begins
`---`), JSONL version sidecar as counter source of truth, atomic writes,
symlink containment, `redactions.jsonl` erasure trail (user-decided);
`MemoryStoreContract` (~30 tests incl. parametrized grammar/round-trip
tables, `plant_raw_document` substrate hook) run green over FileStore;
`Clock`/`SystemClock` (first UTC discipline); `neosian.memory` +
`neosian.memory.testing` lazy public surface, root `__all__` +12; the
memory ↛ provider-internals import contract (ledger #18:
`exclude_type_checking_imports`). 1204 unit tests, zero keys.

**Slice B shipped at v0.58.0 (2026-08-19), closing the phase** — the
memory layer speaks: one `memory` function tool carrying the six-command
vocabulary (ledger #19 — the packaging changed, never the vocabulary),
`Mount`/`MemoryConfig` + the virtual path space (mounts as top-level
directories), read-only enforcement wiring `MemoryReadOnlyMountError`,
the prompt pack in `assets/prompts/memory.yaml`, index generation +
`memory_system_section` (frozen per conversation, caller-injected),
`AgentConfig.memory` auto-registration, the playground injecting the
section in one event loop, `examples/memory_agent.py`, and the done-when
pinned keylessly (two scripted sessions over one FileStore root). Ledger
#20: `create` stays create-or-overwrite with an overwrite reminder.

## N2 — Conversation layer (v0.60–0.62) ✅ 2026-08-20

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

**Split (2026-08-19): slice A shipped at v0.60.0** — the design
discussion opened the phase (options-first: separate `ConversationStore`
ABC, projection surface in the ABC from day one, typed messages + public
codec, `agent_conversation_*` codes, emit-before-terminal core fix) and
landed as DESIGN §9 in full, compaction spec included. Implemented: the
five-method `ConversationStore` ABC (CS1–CS7) + `ConversationTurn` /
`ConversationProjection` + the id grammar; FileStore turn persistence
(`conversations/<id>/turns.jsonl` + `projections.jsonl`, mixin keeps
file.py under the size gate); `ConversationStoreContract` (~26 tests,
`plant_raw_turn` hook) run green over FileStore; `Conversation` —
send blocking/streaming with identical persistence via the on_turn
capture (hook captures, send writes — ledger #25), resume, frozen
index, `actor=conversation_id`, hook composition, caller config never
mutated; the `memory_scope=` sugar mounting at `memories`; register #6
fixed (streamed on_turn now fires before the terminal yield — a
consumer that saw `done` holds a persisted turn); `message_to_json`/
`message_from_json` public (ledger #22); root `__all__` +9,
`neosian.conversation` (+`.testing`) facades; two import-linter
contracts. The slice-A done-when pinned keylessly (resume with memory
across two scripted sessions). 1389 unit tests, zero keys. Carried to
slice B: compaction v1 per §9.6. Carried to slice C: the CLI migration
(+ --menu rebuild drops), examples, optional session reuse/`aclose()`.
Deferred per §9.10: the ECOSYSTEM amendment (two-repo move).

**Slice B shipped at v0.61.0 (2026-08-20)** — compaction v1 per §9.6:
the coverage-keyed view render (ledger #27), the pre-run high-water
trigger, deterministic log lines + batched digest distillation +
model-written epoch folds, the lazy `recall_turn` tool, default-on
`CompactionConfig` (ledger #28), manual `Conversation.compact()`, and
compaction spend folded into the send's response/terminal event
(ledger #29). Ledger #27–#32; §9.6 implementation notes added. Carried
to slice C: the CLI migration (+ --menu rebuild drops), examples,
optional session reuse/`aclose()`; the §9.10 ECOSYSTEM amendment stays
deferred.

**Slice C shipped at v0.62.0 (2026-08-20), closing the phase** — the CLI
dogfoods Conversation. Session reuse per §9.5.14: one internal
`AgentSession` per Conversation (lazy, rebound at the compaction
boundary — never a reconnect), shared by sends and distillation via the
`acquire` lease (ledger #33 — the callee no longer closes what it
acquired); public `aclose()` + `async with`, sticky fallback now spans a
conversation's sends. The playground: chat rides `Conversation` +
`FileStore(cwd/.neosian)` — persist-per-send (crash/^C loses nothing;
compaction default-on means long chats now spend on distillation, the
intended dogfood), generated timestamp+agent ids printed at start,
`--resume <id>` (legacy JSON refused with a naming error), save-at-exit
gone, the in-place system-prompt mutation gone; the nine-field `--menu`
rebuild fix via `dataclasses.replace` (arena's twin keeps `memory=None`
deliberately); the monolith split into `playground`/`chat`/`arena`/
`models`/`ui`, retiring the size-gate allowlist entry; the single-agent
`Session` deleted (Arena types stay — arena remains off Conversation).
`examples/conversation_example.py` (quickstart, resume, memory-scope
sugar). 1471 unit tests, zero keys.

## N3 — PostgresStore (v0.63–0.64) ✅ 2026-08-20

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

**Split (2026-08-20): slice A shipped at v0.63.0** — the store and its
tier. Options-first rulings: psycopg 3 async (typed, mypy --strict clean)
as the first `[postgres]` extra; shipped-SQL migration story (idempotent
`assets/sql/postgres.sql`, `apply_schema()`, `python -m neosian.schemas
postgres [--schema]` — no alembic); the `memory_redactions` erasure-trail
table included; the postgres CI job on every push/PR (service container,
no secret — ledger #40). `_foundation/postgres/` born: one
`PostgresStore(dsn, *, schema="neosian", clock=None)` implementing both
ABCs — pure-validation ctor, lazy autocommit pool, `aclose()`/`async
with`; every mutation a single data-modifying-CTE statement with gate
CTEs + diagnostics (never BEGIN/COMMIT — ledger #34), unique-violation/
deadlock retry realizing CS3's `COALESCE(MAX(turn),0)+1`;
`supports_optimistic_concurrency = True` (the expected_version race is
PK-arbitrated); Clock-sourced timestamps (#35), `COLLATE "C"` listings
(#37), `extra` jsonb round-trip (C6), dormant tsvector FTS hatch, NUL
limitation documented (#38), retry exhaustion propagates the driver
error (#39). Tests: both contract kits inherited green over a real
server (incl. the optimistic-concurrency test FileStore skips) via
per-test schema isolation + `plant_raw_*` adaptations (#36); the CS3
two-pool 25-append gapless race and the one-winner expected_version race
(the store-level half of the done-when); keyless unit tests pin the DDL
asset, the schemas CLI, pure construction, and driver-free imports.
Surface: root/facade `__all__` +1 (`PostgresStore`), `external_postgres`
marker, `make test-postgres`, SERVICES.md DSN section. 1483 unit tests,
zero keys; 100 postgres tests. Carried to slice B: the FastAPI example +
§6 relay + multi-worker demo (the done-when), pool-tuning kwargs.

**Slice B shipped at v0.64.0 (2026-08-20), closing the phase** — the
application-level done-when. Pool-tuning kwargs
(`min_size`/`max_size`/`pool_timeout`) on the ctor: purely validated,
psycopg's own defaults, passed at lazy pool open, arrival pinned by a
recording-driver unit test. `examples/fastapi_chatbot.py` (new
`examples` dependency group): app-factory FastAPI chatbot on
PostgresStore — store per worker (lifespan-owned), Conversation per
request, two mounts per tenant (rw `tenant:{t}/user:{u}` at `memories`,
read-only `tenant:{t}/kb:shared` at `kb`), ids composed
`{tenant}--{thread}`; its relay generator is §6's reference
implementation — the pending `__anext__` lives in a persistent task
across keepalive timeouts (`wait_for` would cancel into the agent's
generator), error frames carry only the code, schema application stays
the operator's explicit act. `test_fastapi_workers.py` (5 tests,
FakeClient agents over real pools, every push/PR): gapless two-worker
appends through the HTTP surface, cross-worker resume, the SSE wire
form, keepalive under a running tool, the code-only error frame with
nothing persisted. Dogfooded live under `uvicorn --workers 2`: two
worker pids served 3 gapless turns with tool messages persisted, and
the agent unprompted wrote `deploy_day` into the per-user mount.
1489 unit tests, zero keys; 105 postgres tests.

## N4 — Completeness (v0.65 → 1.0)

DESIGN: §2, §6, §10.

- Native `memory_20250818` tool type on Anthropic behind a flag (same
  store, provider-native surface; rides the trained behavior).
  *(✅ shipped v0.65.0, slice A)*
- Anthropic server-side compaction as an opt-in where available.
  *(✅ shipped v0.66.0, slice B — agent-level pass-through)*
- **MCP memory server packaging** — neosian memory usable from Claude Code,
  Claude Desktop, Cursor, any MCP client, backed by the same stores.
  Ships as an optional extra — `uv add "neosian[mcp]"`, module
  `neosian.mcp` — one repo, one release train.
  *(✅ shipped v0.67.0, slice C)*
- Memory eval harness built on the existing `evaluation/` module — there is
  no public benchmark for the agent-memory regime (LoCoMo measures the
  personalization regime), so we measure ourselves: write discipline,
  recall-in-next-session, dedup behavior — per provider, FakeProvider
  baselines first.
- Docs and quickstarts. **1.0 = API stability promise** for `Agent`,
  `Conversation`, `MemoryStore` — and the ECOSYSTEM seams move from
  append-only-by-convention to SemVer-guaranteed.

**Done when:** one store serves the same memory through the function tool,
the native Anthropic flag, and an MCP client; the eval harness reports
per-provider memory baselines (FakeProvider first); README and quickstarts
match the tree; `v1.0.0` is tagged carrying the stability promise.

**Split (2026-08-20): slice A shipped at v0.65.0** — the native flag, the
pure transport swap ledger #19 promised. Options-first rulings (user
confirmed): `AgentConfig.native_memory` (survives `derive_config`'s
replace), the internal `ToolDefinition.native_type` marker +
`set_native_type` — never a public `@Tool` param (#41);
inert-never-raises, one warning per condition (#42); the wire description
drops while `memory_system_section` stays verbatim (#43); no capability
field, no fallback gate — the marker degrades by construction, pinned by
one converter test per non-Anthropic client (#44). AnthropicClient emits
`{"type": "memory_20250818", "name": "memory"}` — GA, no beta header,
`cache_control` valid on the native entry (verified against the installed
SDK). Execution, mounts, read-only enforcement and corrective failures
are byte-identical either way, pinned by a native-vs-plain round-trip
test. The reference argument vocabulary is accepted first-class —
`file_text` as `create`'s trained alias for `content`, `view`'s
`view_range` with real line numbers — so native emissions never hit an
unexpected-keyword failure (found against the live tool docs; the raw
`**arguments` dispatch would have TypeError'd). 1519 unit tests, zero
keys. Carried to slice B: server-side
compaction pass-through (the `llm/blocks.py` extraction first — base.py
is one line shy of the size gate otherwise); MCP, eval harness, docs/1.0
in later slices.

**Slice B shipped at v0.66.0 (2026-08-20)** — server-side compaction as
an agent-level pass-through, exactly "opt-in optimization, never the
foundation". Prep: `llm/blocks.py` extracted (pure move, base.py 453 →
333). `CompactionBlock` joins the `ContentBlock` union (#46) with codec
round-trip incl. `content=None`; `AgentConfig.server_compaction` threads
per-call like `cache_conversation` (#45) to all four call sites +
`FakeCall`; AnthropicClient switches to the beta namespace
(`compact-2026-01-12` + `context_management`) only when on — flag-off
requests byte-identical, pinned; compaction blocks parse into ordered
assistant block lists, stream with assignment-semantics deltas onto
`StreamChunk.compaction`, and `assemble_streamed_content` weaves them
into the loops' messages. Spend: the beta reports summarization tokens
only under `usage.iterations` — folded into `Usage` on both paths (#48;
docs-verified). `ModelSpec.supports_compaction_blocks` (True Opus 5 /
Opus 4.6 / Sonnet 5, False Haiku 4.5 — docs-verified, the reason a
provider check would be wrong, #47) gates the pre-flight and the
fallback of a compaction-bearing history ("compaction" joins
`unsupported_content_types`); the other three converters reject the
block (never silently dropped). Conversation warns when the flag is on
under it — log-projection drops the blocks at the warm boundary and the
server would re-bill (#49); §9.6 records the deferral. Assistant
messages now legitimately carry text+compaction block lists (media
still raises) — the one deliberate contract change, in `Message`'s
docstring. Ledger #45–#49. 1551 unit tests, zero keys. Remaining in
N4: MCP packaging, memory eval harness, docs/1.0.

**Slice C shipped at v0.67.0 (2026-08-20)** — memory serves MCP clients
from the same store. Options-first rulings (user confirmed): one
`memory` tool serving `create_memory_tool`'s `ToolDefinition` verbatim
through the SDK's low-level `Server` (#50); flags + env-DSN config
(`--root`/`--scope`/`--mount`/`--actor`/`--schema`,
`NEOSIAN_MCP_POSTGRES_DSN`, no `--dsn` ever, #53); `instructions` =
`memory_system_section` at construction + per-connection lifespan
refresh (#51); stdio only + public `create_memory_server` for
embedders. Prep: the tools.py command ladder extracted to
`memory/dispatch.py` — function tool, native flag, and MCP now execute
one dispatcher (test_tools.py passed untouched). `_foundation/mcp/`
(sdk/server/settings, postgres-driver lazy-import pattern) +
`neosian/mcp/` facade (`__all__ = ["create_memory_server"]`) +
`python -m neosian.mcp` + `neosian mcp` pass-through; ToolResult maps
to native MCP shape (`is_error` in-band, `system_reminder` as second
block, #52); handler catches mirror tool_exec so mistyped values fail
correctively on every transport; entry point owns the store lifetime
(pool closed on exit). `mcp>=2,<3` extra + dev group; new import
contract (mcp ↛ agent/providers); SDK verified against the installed
2.0.0 wheel. Tests: dispatch dict-seam suite, in-process
`Client(server)` sessions, a 16-case byte-equality parity table +
mistyped-value parity, the tri-transport done-when over one FileStore,
entry/settings/exports suites (root and facade imports stay SDK-free
by subprocess pin). Dogfooded over the real stdio wire: scripted SDK
client → create/str_replace/view + read-only corrective + version rows
carrying `actor: mcp:dogfood`. 1641 unit tests, zero keys. Deferred:
streamable HTTP (embedders mount the factory's server); README MCP
snippet waits for the docs/1.0 slice. Remaining in N4: memory eval
harness, docs/1.0.

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
- 2026-08-19 | N0 (slice B) | **The wire freezes; N0 closes.** Events v2
  per §6: nine frozen+slots dataclasses in `agent/events.py` (payload
  TypedDicts double as the `event_schemas()` export source — schema and
  `to_dict()` cannot drift, pinned by a validate-against-schema test;
  ledger #15: payload carries the `event` discriminator), one
  `EventSequencer` stamping pass in `run_streaming` (ReadyEvent first,
  sequence continuity across fallback for free — emitter param deleted
  from five modules), `heartbeat` → `ToolProgressEvent(elapsed_ms: int)`,
  terminals carry `usage_by_model`, `DoneEvent.model` = API-reported,
  compact ASCII wire form, `ErrorEvent.from_exception` (code, no
  message), `sse_stream`, `python -m neosian.schemas events [--out DIR]`;
  `streaming.py` deleted, `__all__` −4/+16. ContextPolicy (ledger #16):
  default-on, underestimating char heuristic in
  `shared/context_policy.py`, checked once per attempt pre-call (no
  spurious on_llm_call), keyless-tested against FAKE_SMALL's 8_192
  window; `ensure_fallback_viable` gained the no-smaller-window gate and
  the no-fallback branches re-raise caller-input errors unwrapped with
  usage attached (ledger #17). Eval: per-case `script:` → FakeTurns →
  injected FakeClient, keyless end-to-end incl. tool expectations;
  throttle skipped for fake/scripted; `_run_conversational` rebuilds
  context from `turn_messages` (fixing the multi-round lumping).
  CLI done-when: `llm/codec.py` (`message_to_json`/`from_json`, content
  inverse), Session full-fidelity save + `load`/`from_dict` +
  `neosian_session: 1` marker, playground persists user msg +
  `turn_messages` verbatim (lost-tool-history bug fixed), `--resume
  PATH` replays. 1013 unit tests, zero keys; v0.56.0.
- 2026-08-19 | N1 (slice A) | **The storage seam.** `_foundation/memory/`
  born: `MemoryStore` ABC verbatim from §8 (no `__init__`, seven abstract
  async methods, `supports_optimistic_concurrency=False`), frozen
  `MemoryDocument`/`MemoryEntry`/`MemoryVersion` (+`extra` — preserved
  unknown keys, output-only), `MEMORY_FORMAT_VERSION = 1`. Scope grammar
  per ECOSYSTEM §2 (`\A…\Z` anchoring — `$` accepts a trailing newline;
  no normalization; `scope_directory` percent-encodes one dir per segment
  so a 512-char scope survives NAME_MAX and `%3A` guarantees no reserved-
  name collision; decomposition pinned to scope.py by grep test) + path
  grammar (bare `.`/`..` only; `..foo` legal). Seven `memory_*` codes
  appended (base `MemoryStoreError`; messages inline, constants.py not
  grown). `FileStore`: exact envelope codec (first-fence-only, `newline=""`
  everywhere, ISO-Z timestamps, naive refused never coerced), JSONL
  sidecar = version-counter truth (delete consumes a number, re-create
  continues, malformed lines raise), atomic same-dir-temp writes, symlink
  containment, in-process `asyncio.Lock` (10 gathered writes → versions
  1..10 gapless), best-effort `expected_version`, `redactions.jsonl`
  erasure trail. `MemoryStoreContract` conformance kit (per-method
  `@pytest.mark.asyncio`, kit-owned `scope` fixture, `plant_raw_document`
  hook, mismatch test gated on the ClassVar) — FileStore inherits it
  green. `Clock` Protocol + `SystemClock` in shared/. Public:
  `neosian.memory` (+ `.testing`, pytest never a runtime dep — pinned by
  subprocess test), root `__all__` +12. Ledger #18:
  `exclude_type_checking_imports` for the new memory ↛ provider-internals
  contract. 1204 unit tests, zero keys; v0.57.0. Carried forward:
  slice B (tools, prompt pack, mounts, index, CLI done-when).
- 2026-08-19 | N1 (slice B) | **The memory layer speaks; N1 closes.**
  Options-first rulings: **one `memory` tool** with a `command` enum
  (ledger #19 — mirrors `memory_20250818`, N4 native flag = transport
  swap; flat all-optional schema, per-command `_require` checks and an
  unknown-command guard fail correctively — Literal is schema steering,
  never runtime enforcement) and **`AgentConfig.memory`** auto-registration (the
  todo/playbook/blackboard idiom; `Any`-typed field like blackboard).
  `memory/mounts.py`: frozen `Mount(scope, mount_path, read_only,
  description)` + `MemoryConfig(store, mounts)` (≥ 1 mount, unique
  mount paths — explicit scope made structural), `resolve` (first
  segment picks the mount), `writable` — the only
  `MemoryReadOnlyMountError` raise site (slice A's deferral wired).
  `memory/commands.py`: view (`/` returns `generate_memory_index`
  verbatim — one renderer; documents line-numbered; directory = plain
  prefix + `/`; redacted labeled, never empty-looking), create
  (create-or-overwrite + overwrite reminder, ledger #20), str_replace
  (exactly-one; 0/N corrective with match lines) and insert (bounds
  named) both passing `expected_version`, delete, rename (same-mount =
  store.rename; cross-mount composed read+write+delete, documented
  non-atomic). `memory/tools.py`: closure factory, every
  `MemoryStoreError` → `ToolResult.fail("[code] message")` + per-code
  hint, no PEP 563 (decorator resolves hints at decoration time).
  `memory/index.py` + `assets/prompts/memory.yaml` (tool ≤ 1024 chars
  for the OpenAI-compat cap; `system_section` carries discipline +
  routing + `{{index}}`); prompt_assets loads it fail-fast. Playground:
  section injection + Agent construction + chat loop in one event loop
  (`_run_chat`; store locks bind per loop), `--menu` rebuild passes
  `memory=` (its other drops carried to the N2 CLI migration), arena
  deliberately memory-less; `examples/memory_agent.py`. Done-when pinned
  keylessly: two scripted FakeClient sessions over one FileStore root —
  create in session 1, fresh store + index + view in session 2.
  DESIGN §8 gained the tool-layer contract paragraph. Facade `__all__`
  +5, root +4 (`Mount`, `MemoryConfig`, `create_memory_tool`,
  `memory_system_section`); memory.yaml verified in the wheel.
  1277 unit tests, zero keys; v0.58.0.
- 2026-08-19 | N2 (slice A) | **Conversations persist, resume, and carry
  memory.** The phase-opening design discussion ran options-first and
  landed whole as DESIGN §9 (§9.1–§9.10, compaction spec included);
  rulings: separate `ConversationStore` ABC (ledger #21), projections in
  the ABC day-one (#23), typed seam + public codec (#22),
  `agent_conversation_*` under the closed prefix set (#24), capture-in-
  hook/write-in-send (#25), `allow_indirect_imports` + storage-seam ↛
  agent contracts (#26). `_foundation/conversation/` born: id grammar
  (`\A[A-Za-z0-9_.-]{1,128}\Z`), frozen `ConversationTurn`/
  `ConversationProjection`, the five-method ABC (store-assigned gapless
  turn numbers, `read_turns(after, limit)` doubling as the recall
  lookup), `FileTurnStore` mixin (FileStore implements both seams; last
  line is the numbering truth; malformed/newer/naive rows raise;
  `conversations/` collision-free beside `%3A`-encoded scopes),
  `ConversationStoreContract` (~26 tests, `plant_raw_turn`) green over
  FileStore. `Conversation`: sync ctor/lazy start, send blocking +
  streaming persisting identical turns through the on_turn capture,
  resume = same id, frozen index injected once, memory tool rebound
  with `actor=conversation_id` (`memory=None` on the derived config —
  no duplicate tool), hooks composed, exclusive `memory=`/`mounts=`/
  `memory_scope=` (sugar mounts at `memories`, the native-tool root).
  Register #6 found and fixed: streamed on_turn fired after the
  terminal yield — now before it at all three sites (stream_final
  defers the done yield past emit_llm_call, hook order preserved), so
  Conversation persists before relaying the terminal and a consumer
  that saw `done` holds the turn. `Agent.config`/`.max_tool_iterations`
  read-only properties. Codec public; root `__all__` +9 (130);
  `neosian.conversation` + `.testing` facades pinned; +3 error codes.
  1389 unit tests, zero keys; v0.60.0. Carried: slice B compaction
  (§9.6), slice C CLI migration + examples; ECOSYSTEM amendment
  deferred (§9.10).
- 2026-08-20 | N2 (slice B) | **The view pages; compaction v1 lands.**
  §9.6 implemented whole: `projection.py` (coverage-keyed `select` with
  explicit (span, index) max — naive last-write-wins loses a fold to a
  later narrow entry; `render_view` emitting whole turns or one
  synthetic USER log block — never SYSTEM, which the Anthropic adapter
  would swallow as the system prompt; deterministic `log_line` with
  `TOOL name(args) → head/tail`, USER verbatim to 4×digest_chars then a
  `[recall_turn(n)]` pointer), `distill.py` (DigestBatch/EpochBatch
  structured-output calls through the injected `acquire` seam, turn
  numbers explicit in and out so a partial response degrades instead of
  misattributing, every failure → warn + degrade), `compaction.py`
  (`CompactionConfig` default-on + validation, `CompactionResult`,
  `run_boundary`: pending → digests → fixed-aligned epoch folds —
  model-written summaries per the user ruling, a failed fold retried
  next boundary — one `append_projections` batch), `recall.py` (lazy
  tool, corrective failures, store errors → ToolResult.fail). Core:
  `_turns`/`_projections` replace the flat history, pre-run trigger
  (ContextPolicy fallback, ledger #30), boundary rebuilds the derived
  agent = the §9.5.10 index refresh, public `compact()` (ignores
  `enabled` — manual is explicit intent), usage folds via
  `fold_response`/`fold_event` in wiring (ledger #29). All four new
  modules join the storage-seam import contract (agent-free by
  construction, ledger #32). Kit: `plant_raw_projection` + 2 tests;
  file_turns `where` mislabel fixed ("projection line N"). Assets:
  `compaction.yaml` (distill/epoch/log frame) + `tools.recall_turn`
  (≤1024 pinned). Root `__all__` +2 (`CompactionConfig`,
  `CompactionResult`), facade +3. ~70 new tests incl. FAKE_SMALL
  trigger end-to-end, streaming parity, abandonment, resume-compacted.
  1455+ unit tests, zero keys; v0.61.0. Carried to slice C: CLI
  migration (+ --menu drops), examples, session reuse/`aclose()`.
- 2026-08-20 | N2 (slice C) | **The CLI dogfoods Conversation; N2
  closes.** Session reuse: `Conversation` owns one lazy `AgentSession`
  (`_session_for_run`), rebound — not rebuilt — at the compaction
  boundary (`AgentSession._rebind`; `derive_config` carries
  `client_factory`, so the cache stays valid); sends and distillation
  share the pool, which forced the `acquire` lease (ledger #33: distill's
  `finally: close()` would have left a dead handle in the cache); public
  `aclose()` (idempotent release, no send-lock — abandoned streams hold
  it) + `__aenter__`/`__aexit__` (enter does no I/O, §9.5.9 intact);
  sticky fallback now spans sends — all written into §9.5.14. CLI:
  `playground.py` (1104 → 130) split into `chat` (the migration:
  `new_conversation_id` slugging into the §9.4 grammar,
  `resolve_resume` refusing path forms *before* grammar-validating —
  `last.json` is a legal id, `open_chat` building
  `FileStore(cwd/.neosian)` inside the one event loop so
  --help/arena/cancel never mkdir), `arena` (dead `_run_arena_model`
  deleted; `arena_config` = replace + `memory=None`), `models`, `ui`;
  `menu_config` = `dataclasses.replace(base, model=…)` fixing the nine
  silent drops; `Session`/save-at-exit/the in-place system-prompt
  mutation deleted (blocked inputs are display-only per §9.5.5);
  allowlist entry retired. Tests: `test_client_reuse.py` (7, incl.
  boundary-survival + distill-on-the-pool), the lease pin in
  test_compaction, `test_chat.py` (hostile-name ids, path refusal,
  store layout, caller-config-untouched), `test_config_rebuild.py`
  (all nine fields asserted by name); `test_session.py` deleted — its
  N0 done-when persist/replay coverage lives in
  tests/unit/conversation (two-session dogfood + contract round-trips).
  Dogfooded keylessly through the real CLI (scripted FakeClient agent:
  chat, resume-by-id shows "Resumed 2 messages", `--resume old.json`
  exits 1 with the naming error, --help leaves no `.neosian/`).
  1471 unit tests, zero keys; v0.62.0.
- 2026-08-20 | N3 (slice A) | **The relational substrate.** Rulings
  (options-first): psycopg 3 async, shipped-SQL migrations (no alembic),
  `memory_redactions` included, postgres CI on every push/PR.
  `_foundation/postgres/` (driver/pool/schema/statements/rows/
  memory_store/turn_store/store): one `PostgresStore` on both ABCs;
  single-statement CTE mutations on an autocommit pool — gate CTEs
  suppress writes, the top-level SELECT returns written-row +
  diagnostics, so one round trip decides mutate-or-raise and the store
  never owns a transaction (ledger #34); PK-retry realizes CS3 and makes
  `expected_version` race-safe (`supports_optimistic_concurrency=True`);
  Clock timestamps (#35), `COLLATE "C"` (#37), NUL exception (#38),
  driver-error propagation on retry exhaustion (#39). DDL:
  `assets/sql/postgres.sql`, idempotent, `{{schema}}`-rendered;
  `apply_schema()` + `python -m neosian.schemas postgres`; dormant
  tsvector hatch + GIN index; RLS-friendly ownership keys on every row;
  deferred FKs for the parent-upsert-in-CTE pattern. Conformance: both
  kits inherited over a real server (unique schema per test, DROP
  CASCADE teardown; `plant_raw_*` line→column adaptation, #36) — the
  optimistic-concurrency kit test runs for the first time; two-pool
  gapless 25-append + one-winner races pin the store-level done-when.
  CI: `postgres` job with a service container on all triggers (#40);
  `make test-postgres`; `NEOSIAN_TEST_POSTGRES_DSN` in SERVICES.md;
  first `[project.optional-dependencies]` extra, core imports pinned
  driver-free by subprocess test. Dogfooded: the three-line quickstart
  (construct → apply_schema → send/resume with memory) against a docker
  server, keyless. 1483 unit tests; 100 postgres tests; v0.63.0.
  Carried: slice B (FastAPI example, §6 relay, multi-worker demo,
  pool tuning).
- 2026-08-20 | N3 (slice B) | **Two web workers on one conversation; N3
  closes.** Pool kwargs: `PostgresStore(dsn, *, …, min_size=4,
  max_size=None, pool_timeout=30.0)` — pure `ConfigurationError` guards,
  psycopg's own defaults (passing nothing changes nothing), threaded to
  `AsyncConnectionPool` at lazy open; a recording-driver test pins
  arrival plus the unchanged `open=False`/`autocommit`; the
  `_MAX_ATTEMPTS` comment now states honestly that a pool tuned past the
  budget can exhaust it (#39 unchanged). `examples/fastapi_chatbot.py`
  (deps via a new `examples` group — `make install` is `--all-groups`,
  so every CI job gets them): `create_app(store=None, agent_config=None,
  keepalive_seconds=15.0)` — injected store/config for tests, otherwise
  a store per worker from `NEOSIAN_EXAMPLE_POSTGRES_DSN` built in the
  factory (ASGITransport runs no lifespan; construction is pure) and
  closed by lifespan; the schema stays the operator's explicit act (two
  workers would race the DDL). Two routes — streaming POST + history GET
  via `message_to_json` — path params pattern-constrained (hostile input
  → 422); mounts rw `tenant:{t}/user:{u}` + ro `tenant:{t}/kb:shared`
  (scope grammar: every segment is `type:id`); ids `{tenant}--{thread}`
  (`:` illegal in ids). The relay generator is §6's reference
  implementation: keepalive comments on the host's timer with the
  pending `__anext__` held in a persistent task — `wait_for(anext(…))`
  cancels into the agent's generator and kills the stream (sentence
  added to §6) — `except NeosianError` → code-only `ErrorEvent`,
  `finally: cancel()` as the disconnect path. `test_fastapi_workers.py`
  (external_postgres, every push/PR): two app instances on two pools
  drive one conversation concurrently through ASGITransport — gapless
  [1, 2] with both user texts, cross-worker resume via GET (4 messages),
  SSE wire form (event/data framing, sequences strictly increasing from
  1), ≥ 1 keepalive while a 0.2 s tool runs and the stream still reaches
  `done`, error frame `llm_model_failed` with no message and zero turns
  persisted. Dogfood: docker postgres + `uvicorn --workers 2` + curl —
  two worker pids served 3 gapless turns, TOOL messages in history, the
  agent unprompted wrote `tenant:acme/user:ada | deploy_day`; an
  aborted `head -c` stream demonstrated §9.5's
  abandonment-persists-nothing along the way. Docs: DESIGN §6/§8,
  SERVICES.md. 1489 unit tests, zero keys; 105 postgres tests; v0.64.0.
  Carried to N4: nothing new; §9.10 ECOSYSTEM amendment still deferred;
  CI billing note stands.
- 2026-08-20 | N4 (slice A) | **Native memory rides the flag.** The
  transport swap ledger #19 pre-shaped, shipped whole:
  `ToolDefinition.native_type` (internal — never a public `@Tool` param,
  #41) set by `create_memory_tool(native=True)` via `set_native_type`;
  `AgentConfig.native_memory` threads it through both registration sites
  (bare agent + `derive_config`, where it survives the replace);
  `AnthropicClient._convert_tools` emits the schema-less
  `{"type": "memory_20250818", "name": "memory"}` — GA endpoint, no beta
  header, `cache_control` still valid on the native entry (both verified
  against the installed SDK, anthropic 0.122.0). The flag never raises:
  inert with one warning per condition (#42 — a `__post_init__` raise
  would break Conversation's deliberate `memory=None` derivation); the
  wire description drops, `memory_system_section` stays verbatim (#43 —
  the index is data the model cannot have); no `ModelSpec` field, no
  fallback gate — non-Anthropic converters ignore the marker and send
  the function schema, pinned per client (#44). Tests: exact native wire
  shape incl. `betas`-absent, mixed native+function lists, unmarked
  byte-identical regression, factory-call independence, the
  native-vs-plain create/view round-trip, agent/conversation wiring,
  caplog warnings. The reference argument names ship first-class
  (`file_text` alias, `view_range` slicing with real line numbers) —
  the tool-docs check caught that the raw `**arguments` dispatch would
  TypeError on the trained `file_text` emission. DESIGN §2/§8 amended,
  ledger #41–#44. 1519 unit tests, zero keys; v0.65.0. Slice B carried:
  server-side compaction pass-through behind the `llm/blocks.py`
  extraction (shipped as prep).
- 2026-08-20 | N4 (slice B) | **Server compaction rides a flag; blocks
  round-trip.** Prep commit: `llm/blocks.py` extracted (pure move,
  base.py 453 → 333 — the additions would have landed it one line shy
  of the 500 gate). The pass-through: `CompactionBlock(content,
  encrypted_content)` in the `ContentBlock` union (#46), codec branches
  incl. `content=None`; `AgentConfig.server_compaction` → per-call ABC
  kwarg mirroring `cache_conversation` (#45; `FakeCall` records it;
  distill pins False); AnthropicClient's `_stream_manager` picks the
  beta namespace (`compact-2026-01-12`, `context_management` compact
  edit) only when on — flag-off kwargs pinned byte-identical, incl. the
  no-`betas` assertion; `_parse_response` emits ordered block lists
  only when a compaction block exists; streaming handles
  `compaction_delta` with assignment semantics (docs-verified: the
  delta carries the full value) onto `StreamChunk.compaction`, and
  `assemble_streamed_content` weaves blocks into all three streamed
  message builds. Spend: compaction tokens live only in
  `usage.iterations` (docs-verified) — `_compaction_usage` folds them
  into `Usage` on both paths (#48, ledger #29's promise).
  `ModelSpec.supports_compaction_blocks` (Haiku 4.5 False —
  docs-verified; #47) gates pre-flight + fallback ("compaction" joins
  `unsupported_content_types`); openai/groq/cerebras reject the block.
  Conversation warns under the flag (#49 — projection would drop blocks
  and double-pay); §9.6 deferral note, §2/§6 amended. Deliberate
  contract change: assistant messages may carry text+compaction block
  lists (media still raises) — the old raise-test re-pinned to media.
  1551 unit tests, zero keys; v0.66.0. Remaining in N4: MCP packaging,
  eval harness, docs/1.0.
- 2026-08-20 | N4 (slice C) | **Memory serves MCP clients from the same
  store.** Rulings (options-first): one `memory` tool, the low-level
  `Server` serving `create_memory_tool`'s definition verbatim (#50);
  flags + `NEOSIAN_MCP_POSTGRES_DSN` (no `--dsn` — argv is world-readable;
  #53); instructions = `memory_system_section` at construction +
  per-connection lifespan refresh (#51); stdio + public
  `create_memory_server`, streamable HTTP deferred. Prep: the tools.py
  ladder extracted verbatim to `memory/dispatch.py` (`command: object`,
  raw pass-through preserved — mistyped values keep their v0.66.0
  failure modes; test_tools.py passed untouched). `_foundation/mcp/`:
  `sdk.py` (lazy loader, postgres-driver twin, install-hint
  ImportError), `server.py` (async factory — it renders instructions;
  handler catches mirror tool_exec's TypeError/Exception split so every
  transport fails correctively; ToolResult → native MCP shape, reminder
  as second block, #52), `settings.py` (argparse, pure; `--scope` sugar
  = one rw mount at `memories`; mount grammar
  `scope=…,path=…[,ro]`; conflicts are errors, never precedence).
  `neosian/mcp/`: facade (+1 public name), `serve.py` (the asyncio.run
  tier; store built and closed here), `__main__.py`; `neosian mcp`
  typer pass-through forwards argv verbatim (one grammar, argparse owns
  --help). Packaging: `mcp = ["mcp>=2,<3"]` extra + dev group (SDK v2
  verified against the installed 2.0.0 wheel: ctor-registered handlers,
  `Client(server)` in-process, fd-claiming `stdio_server` never entered
  by tests); sixth import contract (mcp ↛ agent + providers). Tests:
  dispatch dict-seam suite (hints all reachable), server/client-session
  suites, 16-case byte-equality parity + mistyped-value parity,
  tri-transport done-when (function tool → native marker → MCP client
  over one FileStore), settings/entry/exports (SDK-free imports by
  subprocess pin), CLI forward test. Dogfood: scripted SDK client over
  the real stdio wire — instructions with the prompt pack, create v1 →
  str_replace v2, read-only corrective + hint, version rows
  `actor: mcp:dogfood`; nested `claude -p` is broken in this env, so
  the Claude-Code-as-host check is queued for a manual session
  (`claude mcp add neosian-memory -- uv run --directory <repo> python
  -m neosian.mcp --root … --scope user:me`). 1641 unit tests, zero
  keys; v0.67.0. Remaining in N4: memory eval harness, docs/1.0
  (README MCP snippet lands there).
- 2026-08-20 | N4 (audit sweep) | **The user's five audit findings, ruled
  or fixed.** (1) §9.10's amendment payload now records the firm ruling:
  ECOSYSTEM §6 blesses the shipped `agent_conversation_*` codes and
  declines a `conversation_` prefix; the changelog row sweeps the two
  unrecorded host-visible deltas (#22 public codec, #46 CompactionBlock)
  — executes at the docs/1.0 slice (kit's §15 side already done,
  its 345851e). (2) Size headroom: the wire-format converters moved
  verbatim to `llm/openai_convert.py` and `llm/cerebras_convert.py`
  (499→420, 491→414; delegate methods keep both test suites untouched —
  the allowlist can't hold an under-500 file, so extraction was the only
  honest move). (3) The streaming hot path no longer awaits `_persist`
  per delta — guarded on `self._captured is not None`, semantics pinned
  by the existing persist-before-terminal tests. (4) `fetch_one_retry`
  logs each lost race at debug (contention presents as a signal, not
  silent latency). (5) DESIGN §8 states the additive-only migration
  story (IF NOT EXISTS, no version table) before 1.0 freezes silence
  into commitment; `tests/unit/conversation/test_encapsulation.py` pins
  the Conversation → AgentSession private reach (`_rebind`,
  `_get_or_create_client`) to its sanctioned sites. 1643 unit tests,
  zero keys.
