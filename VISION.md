# Vision

> Status: **seed** (2026-08-18). This document records the direction and the open
> questions — it is a starting point for discussion, not a committed design.
> Decisions marked ✅ are settled; everything else is open.

## What neosian is for

Neosian exists to make building LLM-based, AI-native applications easy: define
tools, configure an agent, run it — with orchestration, streaming, fallback,
guardrails, and structured output handled by the library. The core stays
**stateless**: the caller owns conversation history, and the agent stores
nothing between calls.

The next frontier is **memory and context engineering** — making neosian agents
capable of working across long horizons and across sessions without giving up
the stateless core. Memory will be an **opt-in layer around the agent**, never
state smuggled into it: *"stateless agentic core, with opt-in memory."*

## Direction (seed): agent-managed memory, MemGPT-style

The direction we are drawn to is the **context-window-as-OS** model
(MemGPT/Letta): the bounded context window is RAM, external storage is disk,
and the **agent itself** pages information between them using tools — reading,
writing, and editing its own memory rather than having a retrieval pipeline
push content at it. Built by us, in-house, with zero heavy dependencies.

A concrete inspiration is **Claude Code's memory**: a directory of small
markdown files (one fact per file, frontmatter metadata) plus a lightweight
index file loaded each session, with the agent responsible for writing,
updating, linking, and pruning its own notes. It is simple, transparent,
debuggable with `cat` and `grep` — and it demonstrably works.

**✅ Decided: recall happens through tools, not prompt injection.**
The agent calls `remember` / `recall` / `forget` (and possibly `edit`) when it
decides to. This preserves the Anthropic prompt-cache prefix (injection would
invalidate it every turn — a direct cost regression), spends tokens only when
memory is actually consulted, gives an audit trail via `tool_calls_made`, and
follows the proven Blackboard/Playbook pattern already in the library.

### Research track: Anthropic provider-native mechanisms

Anthropic ships memory and context-lifecycle machinery worth investigating
before we build parallel versions of it:

- **Memory tool** (`memory_20250818`) — the model operates on a client-hosted
  `/memories` directory through a standardized tool; the storage backend is
  ours to implement. This may be the shortest path to MemGPT-style behavior on
  Claude models, and its command set (`view`, `create`, `str_replace`,
  `insert`, `delete`, `rename`) could shape our own tool design.
- **Server-side compaction** — summarize-and-continue past the context window,
  handled by the API.
- **Context editing** — clearing stale tool results instead of summarizing.

Open question: how much do we adopt natively for the Anthropic provider vs.
build provider-agnostic equivalents (neosian serves four providers — a
memory story that only works on Claude is not the library's story).

## Context-engineering principles we adopt

From the current state of the art (Anthropic's context-engineering guidance,
the write/select/compress/isolate framing):

1. **The context window is a budget.** Every token must earn its place;
   `ModelSpec.context_window` (currently populated but unread) becomes live.
2. **Structured note-taking over raw history.** Progress files, checkpoints,
   and agent-curated notes beat replaying full transcripts. Blackboard and
   Playbooks are already this pattern; memory extends it.
3. **Just-in-time retrieval via tools** over pre-loading (✅ decided above).
4. **Compaction with recall-first summaries** when a conversation approaches
   the window, checkpointed so it runs once, not every turn.
5. **Cache-aware layout.** Stable content first; nothing volatile ahead of the
   cached prefix.

## Infrastructure the code needs first (any direction)

These enablers were identified against the actual code and are required
regardless of which memory shape wins:

| Enabler | Why |
|---|---|
| Turn capture (`AgentResponse.turn_messages`, `.model` is done) | Today the intermediate tool-call messages are not returned, so callers cannot faithfully persist a turn — the CLI already loses tool history between turns |
| Observability hooks (`AgentHooks`: on_turn / on_llm_call / on_tool / on_fallback) | Memory persistence in streaming mode needs a turn hook; also replaces the eval runner's log-scraping fallback detector |
| Session-twin refactor (`client_factory` collapsing the `_with_session` duplicates in `agent/base.py`) | Every insertion point is currently doubled; halves the diff of all memory work |
| Context-window fitting (`ContextPolicy`, sliding window) | Long tool loops blow the window today with no recourse |

## Open questions (to discuss before implementation)

- **Storage**: markdown files à la Claude Code (transparent, grep-able) vs.
  SQLite (indexed FTS5 recall, transactional) vs. both behind one ABC.
  Research task: study Claude Code's memory format and Letta's memory-block
  model side by side.
- **Self-editing memory blocks** (always-in-context, agent-maintained — Letta
  style) vs. **passive store + recall tools** — or a small always-loaded index
  with bodies behind tools (Claude Code's MEMORY.md pattern).
- **Conversation persistence**: does neosian ship a `Conversation` object
  (resume-by-id, append-only store), or does history plumbing stay app-side?
- **Compaction**: neosian-built (cheap model + checkpoint) vs. Anthropic
  server-side where available; what triggers it and what survives it.
- **v1 scope and ordering** — deliberately undecided until the vision firms up.

## What we are not building

- **RAG / embeddings / vector stores** — an application concern; agent memory
  does not need them, and shipping half a RAG stack is worse than none.
- **Batch APIs** — different latency contract from the interactive design.
- **A sync wrapper** — `asyncio.run()` in library code deadlocks in notebooks
  and servers; async-only is a feature.
- **Multi-agent graph runtime** — an `Agent` inside a `@Tool` already
  composes; a graph engine would double the surface for a userland pattern.
- **Agent-editable conversation history** — audit nightmare; compaction covers
  the legitimate case.

## Reference points

- MemGPT / [Letta](https://www.letta.com/) — context-as-OS, self-editing
  memory, paging via tools
- [Anthropic: Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic memory tool, compaction, and context editing (platform docs)
- Claude Code's file-based memory (markdown memory directory + index)
- Mem0 (extraction-based personalization), Zep/Graphiti (temporal knowledge
  graphs), Cognee (graph+vector hybrid) — the framework landscape we chose
  not to depend on, kept here for comparison
