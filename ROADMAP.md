# Neosian Roadmap — Memory & Conversation

> Status: **active** (2026-08-18). Companion to [VISION.md](VISION.md) —
> the vision says *why and what*, this says *in what order*. Version numbers are
> cadence markers, not dates. Each phase ends dogfooded before the next begins.
> Each phase carries a status line (`current` / `pending` / `done`) — machine-read
> by the `/phase` command; exactly one phase is `current` at a time.

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
  (`user:123`, `user:123/project:erp`, `tenant:acme/kb`); hierarchy is a
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
  direction sketched under Phase 2 — refine in a dedicated discussion
  before implementation.)

---

## Phase 0 — Enablers (v0.53–0.55)

**Status: current**

The prerequisites identified in VISION.md, unchanged. Required regardless of
everything below.

| Deliverable | Why |
|---|---|
| Session-twin refactor (`client_factory` collapsing the `_with_session` duplicates in `agent/base.py`) | Every later insertion point is currently doubled; halves the diff of all memory work |
| Turn capture — `AgentResponse.turn_messages` | Callers cannot faithfully persist a turn today; `Conversation` is impossible without it |
| `AgentHooks` (`on_turn` / `on_llm_call` / `on_tool` / `on_fallback`) | Memory persistence in streaming mode needs a turn hook; replaces the eval runner's log-scraping |
| `ContextPolicy` — token counting + window fitting | Makes `ModelSpec.context_window` live; the compaction trigger later |

**Exit criteria:** the CLI can faithfully persist and replay a full
multi-turn session including intermediate tool messages.

## Phase 1 — Memory core (v0.56–0.58)

**Status: pending**

- `MemoryStore` ABC: documents, opaque scopes, version rows, `redact()`.
  Contract speaks in documents/scopes/versions — no filesystem assumptions.
- `FileStore` memory implementation (markdown + frontmatter; JSONL version
  sidecar).
- Memory documents carry a **format-version marker** in frontmatter from
  the first release — the escape hatch for evolving the schema without
  breaking existing stores.
- The six-command tool set as provider-agnostic function tools, operating on
  one virtual path space (mounts appear as top-level directories).
- **The prompt pack** — memory is tool + prompt + index, three pieces:
  write discipline (check before create, update not duplicate, delete what
  proved wrong) and mount routing (user-durable facts up, project facts
  local), modeled on Claude Code's.
- Index generation + injection (frozen per conversation).

**Exit criteria:** an agent in the CLI demonstrably accumulates memory in
one session and uses it in the next.

## Phase 2 — Conversation layer (v0.59–0.61)

**Status: pending**

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
    comes from digest length and epoch folding, measured by the Phase 4
    harness.
  - Distillation calls report through the existing cost accounting
    (`Usage.cost`) — compaction spend is visible, never hidden.
- **CLI migrates onto `Conversation` + `FileStore`** — the dogfood that
  also fixes its lost-tool-history bug.

Explicitly **not** in scope: branching/forking, history editing (ruled out
in VISION.md), transport, auth.

**Exit criteria:** the three-line quickstart — construct store, construct
conversation, `send()` — yields a stateful, memory-bearing agent.

## Phase 3 — PostgresStore (v0.62–0.64)

**Status: pending**

- Schema: `conversations`, `turns`, `memories`, `memory_versions`; scope
  column on every memory row; row-level-security-friendly layout.
- Optimistic concurrency for multi-worker web deployments (the thing files
  cannot do and the reason this substrate exists).
- FTS escape hatch (`tsvector`) behind the same recall interface — dormant
  until a tenant's memory outgrows index-scan-plus-grep.
- Migration story (SQL files or alembic — decide small).

**Exit criteria:** two concurrent web workers on one conversation behave
correctly; an `examples/` FastAPI chatbot demonstrates the multi-tenant
integration end to end.

## Phase 4 — Completeness (v0.65 → 1.0)

**Status: pending**

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
  recall-in-next-session, dedup behavior — per provider.
- Docs and quickstarts. **1.0 = API stability promise** for `Agent`,
  `Conversation`, `MemoryStore`.

---

## Risks

- **Trained-behavior asymmetry.** Anthropic models are post-trained on the
  memory command set; other providers' models less so. The prompt pack
  carries more weight off-Anthropic — the Phase 4 eval harness measures
  this per provider instead of assuming.
- **Conversation-layer scope creep.** `Conversation` is where frameworks
  bloat. The not-in-scope list in Phase 2 is a commitment, not a
  suggestion; anything beyond append-only + resume + memory + compaction
  needs a vision-level discussion first.
- **Solo-maintainer bandwidth.** Defense: every phase is small, shippable,
  and independently useful — the library is already better off if the
  roadmap stops after any phase.
