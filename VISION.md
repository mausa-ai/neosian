# Vision

> Status: **decided** (2026-08-18). This document states the direction and the
> principles behind it. The detail — decided constraints, phases, exit
> criteria — lives in [ROADMAP.md](ROADMAP.md), which is the source of truth
> for execution. When the two disagree, the roadmap wins.

## What neosian is for

Neosian exists to make building LLM-based, AI-native applications easy: define
tools, configure an agent, run it — with orchestration, streaming, fallback,
guardrails, and structured output handled by the library. The core stays
**stateless**: the caller owns conversation history, and the agent stores
nothing between calls.

The next frontier is **memory and context engineering** — agents that work
across long horizons and across sessions without giving up the stateless core.
Memory is an **opt-in layer around the agent**, never state smuggled into it:
*"stateless agentic core, with opt-in memory."*

## The destination

`uv add neosian` gives Claude-Code-grade, agent-curated memory on any of the
four providers, against a plain file directory or your own Postgres — history
persistence, cross-session memory, and compaction with configurable defaults:

```python
store = PostgresStore(dsn)          # or FileStore(path)
convo = Conversation(agent, store=store,
                     conversation_id="thread-829",
                     memory_scope="user:1234")
resp = await convo.send("Where did we leave off?")
```

`Agent` stays exactly what it is today; `Conversation` is the opt-in stateful
shell that owns history, memory, and compaction.

## The school we chose

Of the memory schools in the field — extraction pipelines over vector stores
(Mem0), temporal knowledge graphs (Zep), self-editing context blocks (Letta) —
we build the one Claude Code proves daily: **agent-managed, file-school
memory**, MemGPT's context-as-RAM idea in its most transparent form.

- **The agent is the memory system.** It decides what to remember, writes and
  edits its own small documents (markdown + frontmatter), maintains an index,
  and prunes what proved wrong. Frontier models are now post-trained to do
  exactly this; neosian provides the surface and rides the training.
- **Anthropic's memory command vocabulary is the universal interface** —
  `view`, `create`, `str_replace`, `insert`, `delete`, `rename` over a virtual
  path space — implemented as plain function tools on every provider, with
  native `memory_20250818` wiring on Anthropic as a later optimization.
- **Recall happens through tools, not prompt injection.** A small index is
  injected once per conversation (cache-safe, frozen for the session); bodies
  stay behind tools until the agent asks. Tokens are spent only when memory is
  consulted, and every access leaves an audit trail.
- **Memory is layered like Claude Code's**: history keys off the conversation;
  memory keys off **mounts** — user-level, project-level, shared read-only —
  named by opaque scopes the application chooses. Every mutation is versioned
  and redactable from day one.

## Compaction: paging, not deletion

History is an append-only log; compaction changes what is *in context*, never
what is *stored*. Aged turns are projected to typed one-line log entries —
a three-paragraph question becomes one line with a turn-ref — and a built-in
tool re-hydrates any entry verbatim on demand. Recent turns stay hot and
verbatim; aged turns cool into log lines; the oldest fold into epoch
summaries. No blind transcript summarization, no irreversible loss, cost that
scales with new turns rather than total history.

## Context-engineering principles

1. **The context window is a budget.** Every token must earn its place.
2. **Structured note-taking over raw history.** Curated notes, checkpoints,
   and log projections beat replaying transcripts — Blackboard and Playbooks
   already embody this; memory and compaction extend it.
3. **Just-in-time retrieval via tools** over pre-loading.
4. **Cache-aware layout.** Stable content first; nothing volatile ahead of the
   cached prefix; the compaction boundary is the one free refresh moment.
5. **Transparent substrates.** Memory you can read with `cat`, grep, diff in
   git, and audit row by row — on files in development, on Postgres in
   production, behind one storage interface.

## What we are not building

- **RAG / embeddings / vector stores** — an application concern; agent memory
  does not need them, and shipping half a RAG stack is worse than none.
- **Blind summarization compaction** — irreversible, monolithic,
  unpredictable; log-projection replaces it.
- **Batch APIs** — different latency contract from the interactive design.
- **A sync wrapper** — `asyncio.run()` in library code deadlocks in notebooks
  and servers; async-only is a feature.
- **Multi-agent graph runtime** — an `Agent` inside a `@Tool` already
  composes; a graph engine would double the surface for a userland pattern.
- **Agent-editable conversation history** — audit nightmare; compaction covers
  the legitimate case, and it never edits the store.

## Reference points

- Claude Code's file-based memory (markdown directory + index + prompt
  discipline) — the proven reference implementation of the school we chose
- MemGPT / [Letta](https://www.letta.com/) — context-as-OS, paging via tools
- [Anthropic: Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic memory tool (`memory_20250818`), server-side compaction, context
  editing, and Managed Agents memory stores (versioning/redaction precedent)
- Mem0, Zep/Graphiti, Cognee — the extraction/graph schools we deliberately
  did not adopt, kept here for comparison
