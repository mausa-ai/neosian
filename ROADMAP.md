# Neosian Roadmap

> **▶ Current phase: NR — Reflection**
>
> *(2026-08-21: the 1.0 arc opens — see "The 1.0 arc" below. The lane is
> ruled: neosian is the **state layer for production agents** — durable
> conversations, agent-curated memory, context lifecycle — on storage
> the product owns. Sequencing (user ruling, revised same day after
> cross-session review): NA→NV first (agent-native surface, then
> evidence), NR→NG (the lane's core), NP→NT (product surface; NP
> carries the write-events ECOSYSTEM amendment as the next deliberate
> session-pair — the counterpart is ready), then **NM as the
> capstone** — the state process, wire-frozen last so one promise at
> NZ covers library, seams, and wire — NC background slices
> interleave, NZ declares 1.0. The 1.0 formula has **three gates**:
> lane core + product surface + the process complete, baselines
> published, and a real host vendored and green (the kit vendors the
> already-frozen library seams early — it never waits for the server).
> NM is the Redis-shaped ruling (2026-08-21): the library ships the
> standalone state process (container, streamable-HTTP MCP,
> `RemoteStore`); neosian.com is a separate project — a future host
> that operates the container, never library scope. Arc 1 (NH→N4) stands ✅ whole at
> v0.70.0; that release was first cut as v1.0.0 and withdrawn same day
> (ledger #73) — the stability promise rides the eventual v1.0.0, cut
> at NZ and nowhere else. The v0.70.0 amendment's session-pair
> completed same day (kit ledger #206, its 73dcd39).)*
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

- Phase ids: `NH`, `NS`, `NE`, `N0`–`N4` (arc 1); `NA`, `NV`, `NR`, `NG`,
  `NP`, `NT`, `NM`, `NZ` + the `NC` background track (the 1.0 arc);
  `NW` — the provider-gate track (step 0 immediate, the slate
  post-NZ). Commit
  subjects `<ID>: <what became true>` (≤ 72 chars); `meta:` for repo
  housekeeping. The `v:X.Y.Z. …` subject style is retired (DESIGN §12 #7).
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

*(Shipped whole across N1–N3; the 1.0 arc below completes the lane
around it.)*

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
      key checks *(suites restructured by provider dir; two provider
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
      pinned; wrap gained keyword-only `model=` — DESIGN §5; a 413
      overflow-classification gap recorded as ledger #13)*
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

## NE — Evaluation quality (v0.68–0.69) ✅ 2026-08-21

DESIGN: §2, §5, §7, §10 — plus the evaluation § this phase writes.

`evaluation/` is the last pre-constitution subsystem — functional and
tested (8 unit files; N0 moved it onto hooks + FakeProvider) but below
the bar every phase since NH enforced, found by the 2026-08-20 harness
planning session, which stopped rather than build on it. The 1.0 stake:
`EvalCase`/`EvalConfig`/`EvalTurn`/`EvalResult`/`Expectation` are
root-public while the runner is private — silence at v1.0.0 would freeze
that asymmetry by accident. **Mandate: reform-or-rewrite** — the phase's
opening options-first design discussion decides how deep to cut; a full
rewrite is sanctioned if that is the honest path to ecosystem-grade code.
`eval_*` error codes stay append-only regardless.

- [x] A DESIGN section for evaluation, written options-first in-phase: case
  schema, scoring model, the runner's seams. Kills §2's dangling "§C5"
  cross-reference along the way. *(§13.1–§13.13; §C5 → ECOSYSTEM §7)*
- [x] A public facade — lazy `neosian.evaluation` with run/load entry points
  (today the only runner path imports `_foundation.evaluation`, which
  the CLI does); the root `__all__` change lands as a deliberate,
  pinned diff. *(facade-only ruling, ledger #54: root −7, 33 facade names)*
- [x] Frozen eval config types + a replace-not-mutate runner: the loaded
  `AgentConfig` is never mutated in place (model/hooks/client_factory —
  the N2 `--menu` bug class), pinned by a caller-config-untouched test.
- [x] Mock-seam redesign: `mock_agent_tools`' private reach (`agent._tools`
  rewrite, per-turn re-wrap, `_tool_definitions[i]` surgery) retired in
  favor of hook-based observation — the memory harness's no-mocking
  `AgentHooks` architecture is the reference — plus an encapsulation pin
  like `tests/unit/conversation/test_encapsulation.py`. *(ledger #59/#60)*
- [x] Scoring semantics ruled options-first: response-text matching
  (`actual_response` is recorded but unmatchable), the silently-ignored
  top-level `expect:` on conversational cases, `str()`-coercion rules,
  and whether an LLM judge ever enters (if yes, its prompt ships as
  `assets/` data per §7). *(strict matchers, no judge — ledger #57/#58)*
- [x] runner.py headroom (482 lines, 18 under the fail-at-500 gate) —
  restructured out of the red zone by whatever shape the redesign takes.
  *(13 modules, largest 264 lines)*
- [x] **Final slice: the memory eval harness**, per the ratified
  2026-08-20 plan (`~/.claude/plans/fizzy-spinning-flask.md`; its four
  design rulings user-confirmed) — write discipline,
  recall-in-next-session, dedup behavior; there is no public benchmark
  for the agent-memory regime (LoCoMo measures the personalization
  regime), so we measure ourselves: FakeProvider baselines first
  (keyless), then per-provider `external_<provider>` runs. The harness
  is the reformed module's first consumer and its acceptance test.
  *(shipped v0.69.0 — see the split block)*

**Done when:** the harness reports per-provider memory baselines
(FakeProvider first, zero keys) through the public facade; no evaluation
code mutates a caller's config or reaches into Agent privates
(encapsulation-pinned); every eval config type is frozen; evaluation has
its DESIGN §; `make lint typecheck test size` green.

**Split (2026-08-21): slice A shipped at v0.68.0** — the full rewrite.
Rulings (options-first, user confirmed): full rewrite incl. schema v2
with the `kind:` discriminator (v1 YAMLs break); facade-only public
surface (root `__all__` −7, the fake.py precedent, `EvalError` stays);
strict matchers, no LLM judge; stub-by-default tools with an
`execute_tools` allowlist. Shipped: 13 fresh modules under
`_foundation/evaluation/` (frozen types module-local; the shared/types.py
eval block and constants' `Evaluation` class deleted); one `agent:` +
explicit `variants:` axis replacing the overloaded `prompts:`; typed
equality + matcher vocabulary (`equals`/`contains`/`regex`/`exists`,
response matchers, accumulated failures naming types); `run_case` deriving
via `dataclasses.replace` with composed strict hooks (caller config
pinned untouched) and the agent module loaded once per suite;
`stubs.build_tools` + `attach_tool_metadata` (tools built before
construction — variant description overrides without private reach;
builtins always execute; per-turn `tool_results` stub table replaces the
post-hoc `mock_response` rewrite); `matrix.run_evaluation` → frozen
`EvalReport`; schema-2 JSON artifact; `neosian eval` rides the facade and
exits 1 on failure (a real CI gate); two codes appended
(`eval_config_unknown_key` with the v1→v2 hint, `eval_model_unknown`);
encapsulation pin (`._tools`/`._tool_definitions`/`._tool_metadata`
owner-only) + two import-linter contracts (runtime ↛ evaluation;
evaluation ↛ provider clients); examples rewritten to v2 (`ignore_tools:
[update_todo]`; with_hints folds `on_success` into descriptions).
DESIGN §13 (13 subsections, §13.12 reserves `kind: memory`), §C5 fixed,
ledger #54–#63. 1698 unit tests, zero keys. Carried to the final slice:
the memory eval harness (fizzy-spinning-flask plan, landing as
`kind: memory` per §13.12 — its `mock_agent_tools` workaround is moot).

**Final slice shipped at v0.69.0 (2026-08-21), closing the phase** — the
memory harness lands. Rulings (options-first, user confirmed): transports
occupy the report's variants axis (ledger #64 — reporter/progress/
artifact untouched, schema stays 2); one shipped pack, external tests
derive scriptless copies in code (#68); a `memory=`-bearing base config
is refused as a red cell naming `mounts:` (#66); facade +6 names
(33 → 39, the "everything reachable from an exported config type is
exported" rule stated in §13.9). Shipped: `MemoryEvalConfig` widening
the `EvalConfig` alias + one `isinstance` dispatch in `run_evaluation` —
§13.12's reserved shape exactly; five new modules (`memory_types`/
`memory_loader`/`memory_expectations`/`memory_score`/`memory_runner`)
plus two extractions
slice A's modules now share (`schema.py` kind-neutral parsing,
`capture.py` observation seams); sessions as bare Agents via
`derive_config` (#65) with per-session index regeneration (the
frozen-index rule making recall honest), per-session FakeClients, and
`actor=eval:<scenario>:<session>` on every version row; store-truth
scoring — `documents` (content matchers with response semantics,
version counts, oldest-first actions), `counts` (the dedup signal),
`absent`, `forbidden` (the no-secrets rule) — re-read through a fresh
FileStore; scenario stores at
`.neosian/evals/<ts>-memory/<transport>/<model>/<scenario>` with the
`store root:` line on every red result (#69) and
`run_evaluation(store_root=)` for tests; no new error codes (#70).
`examples/eval_memory_baseline.yaml` (+ its memory-less agent) is the
all-green keyless regression gate (#67); the discriminating negatives —
duplicate doc, wrong mount, answer-without-view, token stored — live in
unit tests; `tests/external/cross/test_memory_baselines.py` runs the
derived scriptless pack per provider weekly, Anthropic additionally on
`transports: [function, native_memory]` — the one informative axis run
(#43/#44). DESIGN §13.12 rewritten in place, §13.2/3/5/9/11 amended,
ledger #64–#70. 1747 unit tests, zero keys.

## N4 — Completeness (v0.65 → 1.0) ✅ 2026-08-21

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
- Memory eval harness — **moved to NE** (2026-08-20 governance session):
  the module beneath it was the last pre-constitution subsystem, so the
  harness lands as NE's final slice, on reformed ground. The measured
  behaviors and the LoCoMo rationale moved with it into NE's section
  above.
- Docs and quickstarts. **1.0 = API stability promise** for `Agent`,
  `Conversation`, `MemoryStore`, and the reformed evaluation surface
  (NE's facade — named deliberately, never frozen by silence) — and the
  ECOSYSTEM seams move from append-only-by-convention to
  SemVer-guaranteed. *(✅ docs shipped v0.70.0, final slice; the 1.0
  declaration itself withdrawn same day and carried to the continuation
  arc — ledger #73)*

**Done when:** one store serves the same memory through the function tool,
the native Anthropic flag, and an MCP client (✅ v0.67.0); the per-provider
memory baselines exist — delivered by NE's harness slice, not here; README
and quickstarts match the tree (incl. the MCP snippet); `v1.0.0` is tagged
carrying the stability promise, evaluation surface included.

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

**(2026-08-20, governance session):** the memory eval harness moved to
NE (see the section above and the session log) — it lands on the
reformed module, as NE's final slice. Remaining in N4: docs/1.0 only.

**Final slice shipped at v0.70.0 (2026-08-21; first cut as v1.0.0,
withdrawn same day — ledger #73), closing the phase and the roadmap** —
the docs slice. Rulings (options-first, user confirmed): the eval
surface's promise lives in the eventual v1.0.0 tag + README, **not** as an
ECOSYSTEM seam (ledger #72 — the recorded §9.10 payload is what the
kit's counterpart signed up for); packaging made PyPI-ready but
deliberately unpublished — the repo stays private, install notes are the
pinned git URL with extras riding it. The §9.10 amendment executed
(ledger #71): ECOSYSTEM §10 gains `ConversationStore` +
`ConversationStoreContract` (FileStore's both-seams role stated), §6
blesses `agent_conversation_*` and declines a `conversation_` prefix,
§11 flips to SemVer-guaranteed from the eventual v1.0.0, the §12
changelog row sweeps
the public codec (#22) and `CompactionBlock` (#46) — its kit cell owed
at kit 345851e; DESIGN §9.10 rewritten to executed, the ABC docstring's
contract-of-record caveat retracted. README rewritten (116 → 287 lines,
16 sections): the opt-in stack in the intro, Conversation as the
headline quickstart, memory/storage/MCP sections (the deferred slice-C
snippet lands), the events-v2 factual fix (the old text claimed
SSE-formatted events with pre-v2 names), a FAKE provider row + the
keyless section, hooks, evaluation incl. `kind: memory`, the Stability
section naming the four promised surfaces. pyproject: 0.70.0 (re-cut),
description/keywords/classifiers/urls; the stale "Stateless agentic"
tagline corrected at its three code sites; CLAUDE.md/SERVICES.md present
tense. Verified: 1747 unit tests zero keys, mypy --strict, 7 import
contracts, size gate, `uv build` (assets in the wheel), every README
import name root- or facade-pinned.

---

## The 1.0 arc — the state layer completed

**The lane (ruled 2026-08-21):** neosian is the **state layer for
production agents** — durable conversations, agent-curated memory, and
context lifecycle — on storage the product owns. Not orchestration, not
RAG, not provider breadth as a goal. The moat is threefold: **the
school** (agent-curated, file-shaped, cache-safe memory as an embeddable
library — the only one), **the receipts** (version rows with actors,
redaction preserving the audit skeleton, integer-µ$ spend — structural,
not bolt-on), and **the measurement** (the store-truth harness — the
seed of the agent-memory benchmark nobody else has).

**The 1.0 formula — three gates, all required** (this supersedes any
earlier two-gate phrasing):

1. The lane's core, the product surface, and **the process** complete —
   NR, NG, NP, NT, and NM (the capstone) shipped whole.
2. Per-provider memory baselines published and standing — NV.
3. **A real host vendored a `v<X.Y.Z>` tag and is green against it** —
   the kit's P10 plus its CI.

`v1.0.0` is cut at NZ and nowhere else; its annotation carries ledger
#72's promise text.

### Decided constraints (the arc)

- **Agent-native principle.** An agent can discover, learn, configure,
  operate, and debug neosian with no human in the loop: docs travel in
  the wheel and are version-true; every CLI command has a
  non-interactive path and `--json` output; every failure names its
  fix. Machine-shaped, never agent-flavored — subcommands, not a chat
  REPL.
- **The shell is a transport.** The memory CLI is the fourth transport
  over the same `memory/dispatch.py` dispatcher, measured on the
  harness's transports axis like the other three.
- **Seam changes stay deliberate.** NP's write-events amendment (§5
  event vocabulary) is the next ECOSYSTEM session-pair; the counterpart
  is ready — either repo may still refuse. No seam moves outside §12.
- **Background never blocks the lane.** NC slices land inside whichever
  phase session has headroom (or a mini-session of their own), each
  with its own done-when; a lane phase never waits on one.
- **Options-first holds.** NR, NG, NP, and NT each open with a design
  discussion; the FileStore cross-process ruling opens NA.
- **Measured, not asserted.** Prompt-pack and memory-behavior changes
  re-run the baselines; scale claims come from harness scenarios, never
  prose.
- **Two first-party stores, forever-custody priced.** FileStore and
  PostgresStore are the only shipped substrates (SQLite stays
  deferred-but-first-in-line, demand-triggered); no first-party
  Mongo/Dynamo/MySQL store and no generic SQLStore, ever — a store's
  correctness lives in the parts SQL doesn't standardize (ledger
  #34/#37/#38 are the argument), and a half-maintained store is a
  compliance bug in the moat. Breadth = the daemon (the appliance for
  every other stack) + NC5-certified community stores.

## NA — Agent-native surface (v0.71) ✅ 2026-08-21

DESIGN: §7, §8 — plus the agent-surface § this phase writes.

The two doors an agent walks through first — the shell and the docs —
built to the standard of the three runtime transports. Agents are
becoming the package-choosers: a library an agent cannot learn inside
its sandbox loses to a worse one it knows from training.

- Opens by writing down the **FileStore cross-process ruling**
  (pre-decided 2026-08-21): documented one-writer per root — no
  advisory-lock engineering; multi-writer needs route to Postgres or to
  NM's daemon, the arc's capstone. Postgres is already arbitrated.
  *(✅ slice A — §8 + ledger #74)*
- **Memory CLI** — the six commands as `neosian memory <command>` over
  the shared dispatcher; flag grammar reuses the MCP settings module
  (`--root`/`--scope`/`--mount`/`--actor`/`--schema`, env DSN, no
  `--dsn` ever); `--json` on every command; corrective failures with
  hints; version rows carry `actor: cli:<...>`. *(✅ slice A)*
- **In-package docs** — `neosian docs [topic]` reads curated pages from
  the wheel (prose as data, `assets/docs/`); `llms.txt` at the repo
  root and in the package; version-true by construction.
  *(✅ shipped v0.71.0, slice B — five pages, §14.4)*
- **Self-setup** — `neosian mcp install --client
  claude-code|claude-desktop|cursor`: prints the exact registration by
  default, applies only with `--write`; every command gains a fully
  non-interactive path. *(✅ shipped v0.71.0, slice B — §14.5)*
- The harness gains the `cli` transport on the transports axis.
  *(✅ slice A)*

**Done when:** a coding agent with shell access alone can discover
(`llms.txt`), learn (`neosian docs`), operate memory (`neosian memory`,
`--json`), and offer the MCP upgrade — pinned by a scripted keyless
walkthrough; the baseline pack is green on
`transports: [function, cli]`.

**Split (2026-08-21): slice A landed (no release — v0.71.0 rides the
phase close)** — the shell-is-a-transport core. The opening act: the
one-writer ruling written into DESIGN §8 + ledger #74 (advisory locks
declined; file.py points at the rule). The store grammar extracted to
`_foundation/memory/settings.py` (#75, the #50 precedent one level up;
`format_mount` is the parser's inverse) and the env key hard-renamed
`NEOSIAN_MCP_POSTGRES_DSN` → `NEOSIAN_POSTGRES_DSN` for every argv
entry point (#76, user ruling, no alias); new import contract — the
foundation never imports the facades (8 kept). `neosian memory`
shipped: the async stream-injected engine in
`_foundation/memory/cli.py` (flags are the dispatcher's names
kebab-cased, `view` defaults `/`, `-` reads stdin, deliberately no
`file_text` alias), entry tier `neosian/memory/cli.py` + `python -m
neosian.memory`, verbatim typer pass-through (the `mcp` shape);
`--json` prints `ToolResult.to_json()` verbatim at exit 0/1, argv-tier
2, interrupt 130 (#77; §14.1–§14.2 written). `build_memory_tool`
splits the one wire definition from execution; the 16-case parity
table runs the CLI (14 rows byte-equal, the 2 grammar-tier rows at
exit 2 — the tiering, not drift); the tri-transport test becomes one
store / **four** transports. `Transport.CLI` on the harness axis:
`evaluation/memory_cli.py` runs the engine in-process (#78 —
user-ruled; the fork layer is scenario-independent, the walkthrough
will pin the real binary), the runner passes `memory_config=None` +
`extra_tools` (exactly-one-tool pinned), and the shipped pack flips to
`transports: [function, cli]` — all-green keylessly, 6/6 by hand
through `neosian eval`; provider baselines inherit the cli cells.
1804 unit tests, zero keys. Carried to slice B: `neosian docs` +
`assets/docs/` (the topology page carrying the 2×2 + #74), `llms.txt`
×2 byte-pinned, `neosian mcp install --client … [--write]`
(refuse-missing-dir ruled), the widened walkthrough — all six
commands through the real binary, the phase done-when — §14.4–§14.5,
README rows, v0.71.0 + `na-done`.

**Slice B shipped at v0.71.0 (2026-08-21), closing the phase** — the
docs door and self-setup. Rulings (options-first, user confirmed):
**five** docs pages (`quickstart`/`memory`/`cli`/`mcp`/`topology`);
claude-code's `--write` targets the project `./.mcp.json` (evidence
`~/.claude`); the registration `command` is `sys.executable` + `-m
neosian.mcp` (ledger #81). Shipped: `shared/docs_assets.py` (markdown +
frontmatter under `assets/docs/`, ECOSYSTEM §8's document clause; the
topic tuple is both reading order and manifest, fail-fast at import,
made honest under lazy CLI import by the load-every-page test) +
`neosian docs [topic]` (`_cli/docs.py`, no rich — bodies pipe byte-
exact; unknown topic exit 2 as text even under `--json`, §14.1's
asymmetry); the topology page carries NM's 2×2 + the #74 one-writer
rule, daemon marked unshipped. `llms.txt` ×2 byte-pinned with the
install pin tied to `__version__` (#79 — the stale-README-pin mode
closed; the v0.70.0 pins had survived a release). `neosian mcp
install`: `_foundation/mcp/install.py` over an injected `Environment`
(first `sys.platform` branch; SDK-free, subprocess-pinned), routed as
`argv[0] == "install"` before the flat server grammar, `prog` threaded
so `--help` names the real spelling; the entry re-renders resolved
settings via `format_mount` (#75's promised consumer), absolute root,
never the DSN (env hint instead); key-preserving merge that refuses —
never rewrites — unparseable configs, and refuses a missing client dir
at exit 1, never mkdir (#80). `StreamParser` promoted to
`memory/settings.py` (the shared argv home). The walkthrough
(`tests/unit/cli/test_walkthrough.py`, no skip path) drives the
literal binary through discover → learn → operate (all six commands, a
stdin pipe, `--json` parsed, exit-2 tiers, the DSN conflict) → upgrade
(print writes nothing; refusal creates nothing), with store truth
(actor `cli:walkthrough`) read back across the process boundary — the
phase done-when. DESIGN §14.4–§14.5, ledger #79–#81; README (shell +
docs sections, install rows, stale transports-axis text fixed) +
CLAUDE.md rows. 1873 unit tests, zero keys; v0.71.0 + `na-done`.
Nothing carried — NA closes.

## NV — Evidence (v0.72) ✅ 2026-08-21

DESIGN: §13, §3.

The harness starts earning its keep as the benchmark.

- **First real baseline runs** —
  `tests/external/cross/test_memory_baselines.py` dispatched per
  provider (they have never run: zero schedule/dispatch CI runs exist),
  then standing weekly. *(✅ secrets set value-blind after per-key
  smoke tests; CI dispatch run 32496965995 — the first ever — plus
  local per-provider sweeps of the grown pack)*
- **Published numbers** — `BASELINES.md`: per-provider, per-transport
  results with run dates and methodology; self-measured benchmarks
  invite motivated reasoning, so the derivation from the shipped pack
  is stated, not implied. *(✅ methodology, fingerprints, per-provider
  tables, and the run-informed calibrations recorded beside them)*
- **Harness-gated prompt pack** — no `memory.yaml` change without a
  recorded baseline re-run. *(✅ tests/unit/test_baselines.py: sha256
  fingerprints of memory.yaml AND the shipped pack recorded in
  BASELINES.md, compared on every `make test` — user-ruled both files)*
- **Scenario growth** — contradiction handling, long-horizon recall,
  correcting a wrong memory; discriminating negatives stay in unit
  tests (the #67 idiom). *(✅ pack 3 → 6 scenarios; three new negatives
  in test_memory_scripted.py)*
- **OTel exporter** — one optional module (`otel` extra) emitting spans
  from the four `AgentHooks`; demonstrated keylessly against an
  in-memory exporter. *(✅ `neosian.otel.otel_hooks()`, flat post-hoc
  spans, api-only extra, facade-only surface — ledger #83)*

**Done when:** BASELINES.md exists with real per-provider numbers and
dates; the weekly runs stand; the OTel extra emits spans keylessly.

**Shipped v0.72.0 (2026-08-21), one session.** The first real runs were
the phase's own best evidence: all four providers went red on the
v0.71.0 pack while behaving well — the pack pinned document *names* the
models were never told about. The finding became `path_prefix`
expectations (exactly one matching live document under the region —
ledger #82, user-ruled) plus four further run-informed calibrations
(provenance annotations kept, filing granularity freed, wordform pins
→ stems/regexes, the correction scenario's record turn made explicitly
memory-worthy), each recorded in BASELINES.md with the fingerprint gate
sealing the pack they produced. Final numbers: Anthropic 6/6 on all
three transports, OpenAI 6/6 on both, Cerebras 6/6 on both, the
since-removed fourth provider 3/6 (stochastic invalid tool-call
emissions on the same weights Cerebras passes clean — an
inference-stack difference; the trained-behavior asymmetry, measured;
NW step 0), and the model registry's first catalog findings
(gpt-5-pro is Responses-API-only; FAKE models now skip the catalog
smoke test).
Document-set write policy noted into NP; eval `seed:` into NG; NC6
(external yardstick, LongMemEval candidate) joins the background
track.

## NR — Reflection (v0.74)

DESIGN: §9 — plus the reflection § this phase writes.

Session-boundary auto-memory: the missing link between the history
layer and the memory layer.

- Opens with the options-first discussion: trigger (`aclose()` vs the
  compaction boundary vs explicit), default-on vs opt-in, the
  consent/review surface, composition with the frozen-index rule, spend
  visibility.
- End-of-conversation distillation — "what from this session is worth
  keeping" — as deliberate writes through the shared dispatcher:
  audited (`actor = conversation_id`), dedup-disciplined (check before
  create), batched structured output (the distill.py idiom), µ$ folded
  into the close's accounting.
- Harness scenarios: boundary writes happen, dedup respected, nothing
  secret stored.

**Done when:** a Conversation that never explicitly wrote memory ends
its session and the store holds the right facts — scripted keylessly;
baselines re-run with reflection on.

## NG — The gardener & scale (v0.75–0.76)

DESIGN: §8, §9.6 (the idiom).

Memory that ages instead of rotting — tractable in the file school,
hopeless in embedding stores.

- Opens options-first: trigger and authority (explicit
  `neosian memory maintain` / a boundary rider / both), the
  deterministic-vs-model split, protection rules (what is never
  pruned).
- **Consolidation pass** — merge duplicates, prune stale, promote
  project→user, confirm-or-decay; deterministic-first, model-batched
  distillation for the rest; every action a version row; spend
  visible.
- **Scale** — index tiering/budgets (log-projection applied to the
  index itself), FTS-hatch activation criteria, measured behavior at
  500+ documents per scope. The harness gains a `seed:` block for
  memory scenarios (documents that exist before session 1, actor
  `eval:seed`) — populating a 500-document store one scripted create
  at a time is not a scenario (noted at NV, 2026-08-21).

**Done when:** a deliberately polluted store (dupes, stale, misfiled)
is measurably improved by one maintenance pass, keylessly scripted; the
index holds its budget at 500 docs; baselines re-run.

## NP — Product surface: governance & write-events (v0.77–0.78)

DESIGN: §8, §6 + ECOSYSTEM §5/§12.

The receipts, exposed — and the arc's deliberate seam change.

- Opens options-first: the governance API shape. The same discussion
  **evaluates a document-set write policy** — "work only within these
  documents / no new doc creation", pre-created layouts the agent may
  edit but not extend (noted for revisit, user ruling 2026-08-21, NV;
  today the only write control is per-mount `read_only`). Screen for
  real use-cases ("only remember these"-style strict deployments)
  first — adopt into the governance surface or decline deliberately,
  never by silence.
- **Governance API** — provenance (turn-ref per fact), point-in-time
  reads exposed, a find-and-redact workflow, the no-secrets write
  guardrail (guardrail layer wired to memory writes — the eval's
  `forbidden` check made preventive).
- **Memory-write events + undo** — write events on the stream so hosts
  render "remembered X" with revert (version rows make undo cheap).
- **The write-events ECOSYSTEM amendment** — §5's event vocabulary
  gains the memory-write event(s): the payload written before the
  session-pair, both ledgers, same pair; the counterpart is ready;
  either repo may refuse.

**Done when:** a host shows a memory write with working undo through
the event stream; redaction runs end-to-end; the amendment stands in
both ledgers.

## NT — The tool-approval gate (v0.79)

DESIGN: §3 — plus the § the design discussion writes.

- The seam design IS the phase: hooks observe and stay observers; the
  gate is its own interception seam (guardrail-style), with
  blocking/streaming parity, timeout and default-deny semantics, and an
  event representation. Options-first before a line of code.

**Done when:** a dangerous tool call pauses for approval and
resumes/denies correctly on both paths, keylessly pinned.

## NM — The state process (v0.80) — the capstone

DESIGN: §8, §9 — plus the § the wire ruling writes.

The state layer as a standalone process: `docker run` serves memory
**and conversations** to any client over the network — the mature
statement of the lane, and the artifact arc 3 (neosian.com, a
**separate project**) will one day operate, never change. Deliberately
last: the wire mirrors the ABCs, so it freezes once, after NP stops
moving them — shipping it post-1.0 would have made the wire a second
stability event; as the capstone, one promise at NZ covers library,
seams, and wire.

**In scope (the ruthless cut):** `neosian serve` (`[server]` extra) +
one published container; MCP over streamable HTTP for agents plus a
store-shaped HTTP API for `RemoteStore` clients; bearer-token auth
only; FileStore and Postgres backends; a health endpoint; graceful
shutdown.

**Explicitly out (v1):** OAuth (the hosted future's swamp),
clustering/multi-node, TLS (a reverse proxy's job), dashboards and org
management (neosian.com). The hosted service later operates this
container; it does not change it.

- Opens options-first with the two rulings the scope cut leaves open:
  **capability mirroring** —
  `RemoteStore.supports_optimistic_concurrency` reflects the backend
  (True over Postgres, False over FileStore); the wire transmits
  capability honestly, never claims it — and **is the wire a seam** —
  an ECOSYSTEM entry (a session-pair) or "the process's API, versioned
  with the library, until a host needs it frozen": ruled deliberately,
  never by silence.
- `RemoteStore` implements both ABCs over httpx (already a core dep);
  the daemon is the multi-writer FileStore answer NA's one-writer
  ruling routes to.
- **Topology, not hierarchy — written into the docs:** two axes (who
  runs neosian code × where the bytes live), four shapes. A single app
  embeds the library and talks to its store directly (files for
  dev/local, Postgres for production — `PostgresStore` is a driver, not
  a process; the DB is existing infra, the SQLAlchemy shape). The
  daemon is the first-class answer when state is **shared across
  processes, apps, or languages** — incl. one container in a dev
  compose beside redis/minio — or when a FileStore needs more than one
  writer; never a proxy an embedded app doesn't need. The quickstart
  still begins embedded — `docker run` is never step one — and the
  daemon adds no capability the library lacks, only reach.
- The harness's transports axis gains `http`; the baseline pack re-runs
  over it.
- **Front-door DX, capstone timing:** for every non-Python or
  non-Postgres stack the appliance IS the first impression — the
  `docker run` → working-memory path is polished like a front door
  (sane defaults, one token, one health check) *within* the frozen
  scope, and NA's topology page gains its co-equal appliance
  quickstart when this phase lands. Placement is untouched — the wire
  still freezes last.

**Done when:** `RemoteStore` pointed at the running container passes
**both contract kits — the same ~58 conformance tests, over the
network** — on both backends (volume FileStore, Postgres DSN), with
the bearer token enforced; one store demonstrably serves five
transports — function tool, native, stdio MCP, HTTP, CLI — through the
one dispatcher.

## NC — the background track (interleaves; never blocks)

Slices, not a phase: each lands inside a phase session with headroom or
as its own mini-session, `NC:` commit subjects, its own done-when.

- **NC1 — MCP client-side.** `tools=[McpServer(...)]` — the agent
  consumes MCP servers as tools. Done when a neosian agent calls a real
  MCP server's tool keylessly (in-process server).
- **NC2 — open model surface.** `register_model(spec)` — custom ids,
  capabilities, pricing, OpenAI-compatible `base_url`; additive to the
  registry. Done when a registered custom model runs the quickstart and
  prices in µ$.
- **NC3 — interop packaging.** "Neosian memory under your
  PydanticAI/OpenAI-SDK agent" example + the data-ownership story
  stated (memory is a directory you can grep, git, and leave with).
  Done when the example runs keylessly against the exported tool
  definition.
- **NC4 — store mobility.** `neosian memory export` / `import` (+ the
  conversation twin): any substrate to any other — FileStore,
  Postgres, the daemon — version history carried, so "leave with your
  data" is true in every direction, not only on files. Natural landing:
  beside NA's CLI verbs or inside NM (onboarding needs it). Done when
  an exported-then-imported store is contract-kit-indistinguishable
  from the original, history included.
- **NC5 — the certification story.** Certify, don't ship: the public
  conformance kits (`neosian.memory.testing` /
  `neosian.conversation.testing`) named as the certification standard,
  an "author a store" guide, and a certified-stores docs page —
  "passes both kits v1" as a machine-checkable badge in the author's
  own CI. Community substrates (Mongo, Dynamo, MySQL…), community
  custody; the contract stays ours. Done when a worked third-substrate
  example in the docs passes both kits.
- **NC6 — the external yardstick.** One accepted public benchmark run
  beside the self-measured baselines (user ruling, 2026-08-21, NV):
  opens options-first — dataset (candidate: **LongMemEval** over
  LoCoMo; its knowledge-update/temporal questions overlap our regime
  where LoCoMo's personalization QA does not), the driver over
  Conversation + memory, judge policy (§13.13: opt-in, never keyless,
  prompt as assets data), spend budget. Done when one
  accepted-benchmark number stands in BASELINES.md beside the
  self-measured tables, methodology stated.

## NZ — The declaration (v1.0.0)

The three gates, checked, then the promise:

- [ ] Gate 1: NR, NG, NP, NT, NM shipped whole (✅ headings).
- [ ] Gate 2: BASELINES.md standing with real per-provider numbers
      (NV, kept current).
- [ ] Gate 3: **a real host vendored a `v<X.Y.Z>` tag and is green
      against it** — the kit's P10 plus its CI.
- [ ] The reach decision taken consciously: public repo / PyPI, or
      deliberately private (the v0.70.0 ruling) — re-affirmed or
      flipped **before** the declaration, never discovered at it; the
      daemon-as-front-door weighting only binds if the flip happens.
- [ ] The re-declaration: README Stability flips to present tense, the
      classifier returns to Production/Stable,
      `make release v=1.0.0` with ledger #72's promise text in the
      annotation.

**Done when:** `v1.0.0` exists, carries the promise, and nothing in it
is asserted without evidence.

## NW — The provider gate (the membership track)

Provider membership is **baseline-gated** (user ruling 2026-08-21 —
the step-0 lesson): a provider row exists only while its memory
baselines stay green, measured per serving stack, never per brand —
the same weights scored 3/6 on the since-removed provider and 12/12
on Cerebras. A
candidate is wired, the shipped pack runs scriptless against it, the
row lands in BASELINES.md; what stays green stays, what does not
exits. The weekly runs are the standing re-test. Sequenced at the
very end deliberately: the slate opens **after NZ** (provider breadth
is not a goal — the lane ruling), and NC2's `register_model` +
OpenAI-compatible `base_url` is the cheap door most candidates enter
through — a first-party client is earned by a green row plus a
feature the compat dialect cannot carry.

- **Step 0 — the fourth provider exited (ruled and ✅ executed
  2026-08-21, same session; v0.73.0).** The membership rule's first
  application: the 3/6 stochastic-emission row retired it, whole —
  client, enum rows, pricing (fingerprint re-pinned), CI lane + repo
  secret, external suites, dependency. The one blocker the audit
  found — guardrails hard-wired to that provider's safeguard model,
  which no other host serves — forced the re-platform ruled
  options-first: configurable `GuardrailsConfig.model`, default = the
  agent's own model, via the `_create_client` seam (keyless on
  FakeProvider, loud at construction). The full record is ledger #84;
  the measured row stands as dated history in BASELINES.md.
- **The slate (user, 2026-08-21)** — a menu, not a queue; each
  candidate enters through the gate above:
  - *Model platforms, mostly OpenAI-compatible (NC2 territory):*
    xAI (Grok), DeepSeek, Moonshot (Kimi), Zhipu (GLM), Alibaba
    (Qwen), Mistral, MiniMax.
  - *Google:* Gemini — native API for full features; the compat
    endpoint is the cheap first pass.
  - *Clouds (a different integration class — auth is the work:
    SigV4 / Entra ID / OAuth):* AWS Bedrock, Azure AI Foundry,
    GCP Vertex.
  - *Open-weight families, not providers:* Gemma, Meta Llama (and
    Qwen/Kimi/GLM/DeepSeek above when third-party-hosted) — a
    family's row always names a provider+model pair; the
    step-0 same-weights split is the proof a family alone measures
    nothing.
  - *On the menu when demand arrives:* Cohere, the aggregators
    (Together, Fireworks, OpenRouter), local serving (Ollama, vLLM),
    NVIDIA NIM.

**Done when (per candidate):** the row stands green in BASELINES.md
over two consecutive weekly runs. **Done when (track):** never —
membership is standing, not achieved.

---

## Risks

- **Trained-behavior asymmetry.** Anthropic models are post-trained on the
  memory command set; other providers' models less so. The prompt pack
  carries more weight off-Anthropic — the NE harness measures this per
  provider instead of assuming.
- **Conversation-layer scope creep.** `Conversation` is where frameworks
  bloat. The not-in-scope list in N2 is a commitment, not a suggestion;
  anything beyond append-only + resume + memory + compaction needs a
  vision-level discussion first.
- **Solo-maintainer bandwidth.** Defense: every phase is small, shippable,
  and independently useful — the library is already better off if the
  roadmap stops after any phase.
- **Cross-process FileStore.** The memory CLI makes multi-process access
  easy; the file substrate's in-process lock does not arbitrate it.
  Ruled (2026-08-21): documented one-writer per root — multi-writer
  needs route to Postgres or NM's daemon; NA writes it into the docs,
  never silently.
- **Benchmark honesty.** Self-measured baselines invite motivated
  reasoning; NV publishes methodology beside numbers, and the
  discriminating negatives stay adversarial in unit tests.
- **Amendment scope creep.** NP's session-pair adds event(s) to a frozen
  vocabulary; the payload is written before the pair and either repo
  may refuse — the §9.10 discipline, repeated.

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
  per-provider `tests/external/<provider>` dirs with path auto-marking,
  `-m "not external" --strict-markers` addopts, consolidated `_key_or_skip`
  (two agent/session suites had **no** skip mechanism — fixed);
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
  longer dropped). Ledger #13 (a 413 overflow gap) and #14 (fakes visible in
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
  `unsupported_content_types`); the OpenAI-compatible converters reject the block.
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
- 2026-08-20 | meta | **Evaluation gets its phase.** The harness planning
  session (same day) audited `evaluation/` and stopped: the last
  pre-constitution subsystem — no DESIGN § (plus §2's dangling "§C5"
  reference), root-public types over a private runner (the CLI imports
  `_foundation.evaluation` directly), in-place AgentConfig mutation (the
  N2 `--menu` bug class), `mock_agent_tools`' unsanctioned
  `_tools`/`_tool_definitions` reach with no encapsulation pin, silent
  scoring footguns (`str()` coercion, unmatchable `actual_response`,
  ignored conversational `expect:`), runner.py at 482/500. Ruled
  options-first: **NE — Evaluation quality** inserted before N4's
  remainder with a reform-or-rewrite mandate (a full rewrite is
  sanctioned); the memory harness moves into NE as its final slice and
  acceptance test — the ratified plan
  (`~/.claude/plans/fizzy-spinning-flask.md`, four rulings
  user-confirmed) stays its implementation source; N4 keeps docs/1.0,
  and the 1.0 promise now names the reformed eval surface so the five
  public types never freeze by silence. Pointer → NE. Docs only; no
  code touched.
- 2026-08-21 | NE (slice A) | **The harness reforged.** Options-first
  rulings: full rewrite + schema v2 (`kind:` from day one, v1 breaks);
  facade-only surface; strict matchers, no judge; stub-by-default with
  `execute_tools`. `_foundation/evaluation/` rewritten as 13 modules —
  frozen module-local types (`AgentEvalConfig`/`EvalCase`/`EvalTurn`/
  `Expectation`/`ValueMatcher`/`Variant` + `EvalReport`/`CaseResult`/
  `TurnResult`/`ToolCallCapture`; shared/types.py:895-1120 and the
  `Evaluation` constants class deleted); strict-keyed loader with
  migration hints (`prompts:`/`mock_response:`/`on_success:`), models
  validated at load; typed-equality matcher (bool≠1, no str coercion,
  int↔float kept, `_exists` sugar, response equals/contains/regex —
  response contains case-insensitive by design, first-call tool rule,
  accumulated failures naming types); `run_case` = replace-derivation +
  composed `strict=True` hooks + `on_tool` single capture path, caller
  config pinned untouched; tools built before construction
  (`stubs.build_tools` + `tools.base.attach_tool_metadata` — the
  `set_native_type` idiom; builtins always execute, `ignore_tools`
  hides them; per-turn `tool_results` set *before* the call so history
  threads verbatim); agent module loads once per suite; `EvalReport`
  carries its own axes; schema-2 artifact; CLI exits 1 on any failure.
  `neosian.evaluation` facade (33 names) + root `__all__` −7
  (`EvalError` stays); +2 codes (`eval_config_unknown_key`,
  `eval_model_unknown`); encapsulation pin + 2 import contracts (7
  kept); examples → v2. DESIGN §13, §C5 → ECOSYSTEM §7, §1/§5 amended,
  ledger #54–#63. 1698 unit tests, zero keys; v0.68.0. Carried: the
  memory harness lands as `kind: memory` (§13.12) — the final slice.
- 2026-08-21 | NE (final slice) | **The library measures its own memory;
  NE closes.** Rulings (options-first, user confirmed): transports on
  the variants axis (#64), one pack + code-derived scriptless external
  configs (#68), `memory=`-bearing base refused red (#66), facade +6
  (39 names, the reachable-types-export rule in §13.9). `kind: memory`
  lands as §13.12 reserved it: `MemoryEvalConfig` widens the alias, one
  `isinstance` branch in `run_evaluation`. Prep extractions shared with
  the agent kind: `schema.py` (check_keys/parse_models/parse_names +
  option parsers), `capture.py` (ToolCapture/FallbackRecorder/
  compose_hooks/scripted_factory), `parse_turns`/`parse_script`/
  `parse_response`/`match_text` made kind-neutral — runner.py 264→176,
  loader reads `kind` before any key. New: `memory_types` (Transport/
  Document-/StoreExpectation/MemorySession/MemoryScenario/
  MemoryEvalConfig + the axis-name property seam progress now reads),
  `memory_loader` (strict keys + agent-kind hints; mount errors
  re-raised in the eval family — no `memory_*` code escapes
  `load_eval_config`), `memory_expectations` (the `expect_store:`
  block, split as expectations.py is from loader.py),
  `memory_score` (four predicates, offenders
  named, fresh-store re-read), `memory_runner` (sessions = bare Agents
  via `derive_config` (#65), per-session index render + FakeClient,
  eval actors on version rows, store failures folded into the last
  turn + `store root:` on every red (#69)); matrix owns the memory
  loop + slugged per-cell roots + `store_root=`. Shipped pack
  `examples/eval_memory_baseline.yaml` all-green keylessly (#67);
  negatives (dup doc, wrong mount, no-view answer, stored token) in
  unit tests; `tests/external/cross/test_memory_baselines.py` weekly
  per provider, Anthropic also native (#43). No new codes (#70).
  Dogfooded: `neosian eval` 3/3 exit 0, artifact schema 2
  `variants: [function]`, stores cat-able, token nowhere in the run
  root. DESIGN §13.12 rewritten, §13.2/3/5/9/11 amended, #64–#70.
  1747 unit tests, zero keys; v0.69.0.
- 2026-08-21 | N4 (final slice) | **The library documents itself; 1.0.**
  Rulings (options-first): eval promise in tag + README, no ECOSYSTEM
  seam (#72); PyPI-ready, never published — private repo, pinned git-URL
  install. The §9.10 amendment executed whole (#71): ECOSYSTEM §10
  +`ConversationStore`/`ConversationStoreContract`, §6 blesses
  `agent_conversation_*` (declines `conversation_`), §11
  SemVer-guaranteed from v1.0.0, §12 row sweeps codec #22 +
  CompactionBlock #46 (kit cell owed at its 345851e), header stamped
  last-amended; DESIGN §9.10 → executed, base.py docstring caveat
  retracted, §9.2's retrofit note now cites §11. README 116 → 287 lines,
  16 sections: opt-in stack in the intro, Conversation headline
  quickstart, memory/storage/MCP (slice-C's deferred snippet), events-v2
  fix (old text claimed SSE-formatted events, pre-v2 names), FAKE row +
  keyless section with the `client_factory` injection, hooks, eval incl.
  `kind: memory`, Stability naming the four surfaces, Documents list.
  pyproject 1.0.0 + description/keywords/classifiers/urls; tagline
  corrected at __init__/constants/CLI; CLAUDE.md + SERVICES.md present
  tense. Verified: make lint/typecheck/test/size green zero keys (1747),
  `uv build` clean with assets, README greps pinned. Tags `n4-done` +
  `v1.0.0` (the stability promise in the annotation). The roadmap
  closes; the kit's P10 vendors from v1.0.0.
- 2026-08-21 | meta | **1.0 postponed; the release re-cut as v0.70.0.**
  User ruling, same day (ledger #73): a version number is a promise —
  1.0 waits for a stronger surface, its continuation arc to be designed
  options-first (candidates: open model registry, MCP client-side
  tools, OTel exporter on the hooks, published per-provider memory
  baselines — the external baselines have never run; no
  schedule/dispatch CI run exists). The `v1.0.0` tag deleted from local
  and origin before any consumer vendored it (the kit's P10 has not
  run); pyproject → 0.70.0, classifier back to Beta, README install
  pins → v0.70.0, the Stability section and §9.10 / ledger #71–#72 /
  ECOSYSTEM §12 row moved to "the eventual v1.0.0". N4's
  1.0-declaration done-when is explicitly carried into the continuation
  arc; everything else N4 shipped stands at v0.70.0. `n4-done` stays —
  the phase's substance closed. Verified: gates green zero keys,
  `uv build` 0.70.0.
- 2026-08-21 | meta | **The 1.0 arc opens.** Strategy sessions (same
  day) ruled the lane — the state layer for production agents on
  storage the product owns — and named the moat (the school, the
  receipts, the measurement). The candidate slate (A lifecycle / B
  product surface / C reach / D evidence / E agent-native) became
  seven phases + a background track: NA agent-native surface (memory
  CLI as fourth transport, in-package docs/llms.txt, self-setup), NV
  evidence (first-ever baseline runs, BASELINES.md, OTel on the
  hooks), NR reflection, NG gardener & scale, NP governance &
  write-events (the next ECOSYSTEM session-pair — counterpart ready),
  NT tool-approval gate, NC1–3 background (MCP client, open models,
  interop), NZ the declaration. Sequencing user-ruled: E+D → A → B,
  C interleaving. The 1.0 formula gains its third gate — **a real
  host vendored and green** — correcting the two-gate phrasing. Also
  today: the v0.70.0 amendment's session-pair completed (kit #206,
  its 73dcd39; the kit-side commit 6aeb7b2 filled the §12 cell) —
  DESIGN §9.10/#71 synced to match. Pointer → NA. Docs only; no code
  touched.
- 2026-08-21 | meta | **NM joins the arc; gate 1 widens.** The
  Redis-shaped ruling: the library ships the standalone memory
  process — streamable HTTP on the MCP server (slice C's deferral),
  `neosian serve`, a shipped container, the `http` transport on the
  harness axis — while neosian.com stays a separate project, a future
  host on the frozen seams (auth/quotas/billing never enter the
  library; the exposure ruling opens the phase). Placed NA→NM→NV so
  NA's cross-process ruling precedes it and NV measures all five
  transports; downstream version markers shifted (NV v0.73 … NT
  v0.79). The 1.0 formula's gate 1 now reads: lane core + product
  surface + **the process** — "the state layer is complete" must be
  testable from outside Python. Docs only; no code touched.
- 2026-08-21 | meta | **NM becomes the capstone — the state process.**
  Cross-session review (brought over from the kit side) adopted whole:
  the wire mirrors the ABCs, so it freezes once, after NP stops moving
  them — placement flips from NA→NM→NV to the capstone slot (NV..NT
  back to v0.72..v0.78, NM takes v0.79); shipping the wire post-1.0
  would have made it a second stability event, muddying the one
  promise. Scope ruthlessly cut in: `neosian serve` (`[server]`
  extra) + one container, MCP over streamable HTTP + a store-shaped
  HTTP API for `RemoteStore`, bearer-token only, health + graceful
  shutdown; out: OAuth, clustering, TLS (reverse proxy), dashboards
  (neosian.com operates the container, never changes it). The
  done-when hardens: `RemoteStore` passes BOTH contract kits (~58
  tests) over the network, both backends — and both kits means
  conversations too, hence "the state process". NA's cross-process
  ruling collapses to documented one-writer (the daemon is the
  multi-writer answer — no advisory-lock engineering); the
  in-process-default discipline written into NM. Two flags for NM's
  opening: capability mirroring (`supports_optimistic_concurrency`
  reflects the backend) and whether the wire is an ECOSYSTEM seam —
  ruled deliberately, never by silence. Kit sequencing unchanged: P10
  vendors the frozen library seams early, never waits for the server.
  Docs only; no code touched.
- 2026-08-21 | meta | **Topology not hierarchy; NC4 store mobility.**
  The daemon discussion resolved into the two-axis rule (who runs
  neosian code × where the bytes live, four shapes): embed when one
  app owns the state — `PostgresStore` is a driver, the SQLAlchemy
  shape, not a third process — and run the daemon, first-class, when
  state is shared across processes/apps/languages (incl. one
  container in a dev compose beside redis/minio) or a FileStore needs
  more than one writer; never a proxy an embedded app doesn't need.
  NM's discipline bullet rewritten from "never the recommended
  default" to this topology rule; the quickstart still begins
  embedded. The 2×2 lands verbatim in NA's `neosian docs` topology
  page. NC4 joins the background track: `export`/`import` across any
  substrate pair with version history carried — the data-ownership
  moat made true in every direction; done-when =
  contract-kit-indistinguishable after the round-trip. Docs only; no
  code touched.
- 2026-08-21 | meta | **Certify don't ship; the appliance is a front
  door.** The kit-side review's carryables adopted, verified against
  our own ledger: NM gains the front-door-DX bullet (the `docker run`
  → working-memory path polished as a first impression *within* the
  frozen scope; the co-equal appliance quickstart lands with NM;
  capstone timing untouched — the wire still freezes last). NC5
  joins the track: the public conformance kits as the certification
  standard + an "author a store" guide + a certified-stores page —
  community substrates under community custody, the contract ours.
  The two-first-party-stores constraint written into the arc: no
  Mongo/Dynamo/MySQL first-party, no generic SQLStore (a store's
  correctness lives where SQL diverges — #34/#37/#38; a
  half-maintained store is a compliance bug in the moat); SQLite
  stays deferred-but-first-in-line. NZ gains the conscious reach
  decision: public/PyPI or deliberately private, ruled before the
  declaration, never at it. Docs only; no code touched.
- 2026-08-21 | NA (slice A) | **The shell is a transport.** Planning
  rulings (options-first, user confirmed): the harness's cli transport
  runs the engine in-process — the fork/exec layer is
  scenario-independent, so the walkthrough pins the real binary once
  per gate instead of taxing every `make test` (#78); the DSN env key
  hard-renamed to `NEOSIAN_POSTGRES_DSN` for every argv entry point,
  no alias (#76); slice B's `mcp install --write` will refuse a
  missing default config dir. Opened with the one-writer ruling
  written down (§8 paragraph + #74 + file.py docstring). The store
  grammar extracted to `memory/settings.py` (#75) — `StoreSettings`,
  `add_store_arguments(default_actor=)`, `resolve_store_settings`,
  `format_mount`; mcp settings is a thin flavor (`ServerSettings`
  aliased, tests pass bar the deliberate env-name edits); the
  foundation-↛-facades import contract added (8 kept). `neosian
  memory`: engine in `_foundation/memory/cli.py` — subparser per
  command with the shared store flags, `_StreamParser` over injected
  streams, typed `_Request` at the parse boundary, store built inside
  the running loop, function-local postgres import — plus entry tier,
  `python -m neosian.memory`, and the verbatim typer pass-through;
  `--json` = the envelope verbatim (#77); DESIGN §14 opened
  (§14.1/§14.2). `build_memory_tool` split (no transport can fork the
  schema); CLI parity: 14 dispatcher rows byte-equal, 2 grammar-tier
  rows at exit 2; tri-transport → four transports. `Transport.CLI` +
  `evaluation/memory_cli.py` (argv from `ARGUMENT_KEYS`, `env={}`,
  `file_text` alias resolved at the boundary, None omitted so the
  grammar answers); runner: `memory_config=None` + `extra_tools` for
  cli cells; pack → `[function, cli]`, `neosian eval` 6/6 exit 0 by
  hand; real-CLI smoke incl. the DSN-conflict exit 2. §13.3/§13.12/
  §14.3 amended, ledger #74–#78. 1804 unit tests, zero keys; no
  release cut — v0.71.0 rides the phase close. Carried: docs +
  llms.txt + `mcp install` + the widened walkthrough (slice B closes
  NA).
- 2026-08-21 | NA (slice B) | **The docs door, self-setup, and the
  boundary closed; NA closes.** Rulings (options-first, user
  confirmed): five docs pages (quickstart/memory/cli/mcp/topology);
  claude-code `--write` → project `.mcp.json` (evidence `~/.claude`);
  registration `command` = `sys.executable` + `-m neosian.mcp` (#81).
  `neosian docs`: `shared/docs_assets.py` reads markdown+frontmatter
  from `assets/docs/` (ECOSYSTEM §8's document clause; the topic tuple
  = order + manifest, fail-fast at import, the load-every-page test
  makes the lazy CLI import honest), `_cli/docs.py` prints bodies
  byte-exact (no rich), unknown topic exit 2 as text even under
  `--json` (§14.1). The topology page lands NM's 2×2 verbatim + the
  #74 one-writer rule, daemon marked unshipped. `llms.txt` ×2
  byte-pinned, install pin tied to `__version__` (#79 — the v0.70.0
  README pins had gone stale; now structural). `neosian mcp install`:
  pure `_foundation/mcp/install.py` over injected `Environment`
  (SDK-free by subprocess pin), routed `argv[0] == "install"` before
  the flat server grammar (`prog` threaded — `--help` names the real
  spelling); resolved settings re-rendered via `format_mount` (#75's
  consumer), absolute root, DSN never written (env hint);
  key-preserving merge refusing unparseable configs, missing client
  dir refused exit 1 never mkdir (#80). `StreamParser` promoted to
  `memory/settings.py`. The walkthrough drives the literal binary
  through all four doors (six commands, real stdin pipe, `--json`
  parsed, exit tiers, DSN conflict; install print-mode writes
  nothing) with the actor read back from version rows — the phase
  done-when, no skip path. §14.4–§14.5 written, ledger #79–#81;
  README/CLAUDE.md rows; pyproject 0.71.0. 1873 unit tests, zero
  keys; v0.71.0 + `na-done`. Pointer → NV.
- 2026-08-21 | NV | **The harness measures real models; NV closes.**
  Key pre-flight one by one (user-ruled; two quota top-ups verified
  live), four repo secrets set value-blind, and the first-ever external
  CI dispatch (run 32496965995) — where all four providers went red on
  the v0.71.0 pack while filing facts *correctly under their own
  document names*: the pack pinned an unspecified naming convention.
  Ruled (options-first): `path_prefix` document expectations — exactly
  one live document under the region satisfies `content`, two is the
  duplicate, `versions`/`actions` apply to the match (ledger #82);
  exact `path:` keeps unit-tier strictness. Four further run-informed
  calibrations (provenance annotations kept, filing granularity freed,
  wordform pins → stems/regexes, correct-wrong-memory's record turn
  made explicitly memory-worthy) — each evidence-driven from cat-able
  stores (#69 paying off), recorded in BASELINES.md. Pack 3 → 6
  scenarios (contradiction, long-horizon-recall, correct-wrong-memory)
  + three negatives. BASELINES.md born: methodology before numbers,
  sha256 fingerprints of memory.yaml + the pack gated by
  tests/unit/test_baselines.py (user-ruled both files), per-provider
  tables — Anthropic 6/6 × 3 transports, OpenAI 6/6 × 2, Cerebras
  6/6 × 2 (14 min on the throttled free tier), the fourth provider
  3/6 (stochastic invalid tool-call emissions on the same weights
  Cerebras passes clean — an inference-stack difference; store-true
  where completed — the asymmetry measured; it exited at NW step 0). OTel: `neosian.otel.otel_hooks()` → plain AgentHooks,
  flat post-hoc spans (gen_ai semconv; never message content,
  arguments, or results), `otel` extra = api-only, facade-only
  surface, 9th import contract, keyless InMemorySpanExporter suite
  (#83). Catalog findings fixed: FAKE models skip the smoke test,
  gpt-5-pro recorded Responses-API-only. Roadmap: NC6 external
  yardstick (LongMemEval candidate), NP gains the document-set
  write-policy revisit, NG the eval `seed:` note. 1899 unit tests,
  zero keys; v0.72.0 + `nv-done`. Pointer → NR.
- 2026-08-21 | meta | **The provider gate; the exit ruled.** Post-NV
  review rulings (user): provider membership becomes baseline-gated —
  the NW track written at the roadmap's end (step 0: the 3/6
  provider exits; the slate:
  xAI/DeepSeek/Moonshot/Zhipu/Alibaba/Mistral/MiniMax platforms,
  Gemini, the three clouds, Gemma/Llama as families, the
  demand-driven menu; per-candidate done-when = two consecutive green
  weekly rows). The removal audit sized the exit (~66 files;
  ECOSYSTEM + kit verified clean) and found the blocker: the
  guardrail engine was hard-wired to the exiting provider's safeguard
  model, which no other host serves — the exit session opens
  options-first there. Docs only; no code touched.
- 2026-08-21 | NW (step 0) | **Groq exits the registry; guardrails
  re-platform.** Same-session execution of the ruling — this entry
  and ledger #84 are the deliberate memory of the removed provider.
  Guardrail engine ruled options-first (user):
  `GuardrailsConfig.model`, default = the agent's own model — the
  classifier prompt runs through a neosian client from the
  `_create_client` seam (#32), honoring `client_factory` (guardrails
  now keylessly testable on FakeProvider, mock-free tests); no
  explicit temperature (some models reject non-defaults); a missing
  guardrail-provider key raises at construction, never a silent
  fail-open (#84). Removal, whole: client module, provider enum row,
  four model rows + specs + pricing (`PRICES_FINGERPRINT` re-pinned),
  the SDK dependency, router branch, CI matrix lane + repo secret,
  the external suites and marker, the CLI's configure/status/picker
  rows, twelve examples' models, one scratch example deleted.
  `AgentConfig.model` default → `CEREBRAS_GPT_OSS_120B`; the cross
  fallback suite re-anchored on Cerebras; catalog test provider map
  trimmed. Docs: README/SERVICES/DESIGN provider prose to three
  adapters, the measured row annotated historical in BASELINES.md,
  NW step 0 ✅, downstream version markers +1 (NR v0.74 … NM v0.80).
  ECOSYSTEM untouched (verified clean; one-repo move). 1869 unit
  tests, zero keys; v0.73.0.
