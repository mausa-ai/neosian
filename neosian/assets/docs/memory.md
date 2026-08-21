---
title: Memory — the file school
summary: Small documents, mounts and scopes, six commands, every write versioned
---

# Memory

neosian memory is agent-curated and file-shaped: small markdown
documents with frontmatter plus an index injected once per
conversation. No embeddings, no vector store — the wrong regime for
agent working memory. You can `cat` it, grep it, git it, and leave
with it.

## Scopes and mounts

Memory is keyed by **mounts** — `(scope, mount_path, read_only,
description)` tuples that appear as top-level directories in one
virtual path space. Scopes are opaque strings of `type:id` segments:

```
user:1234
user:1234/proj:erp
tenant:acme/kb
```

Hierarchy is a naming convention plus explicit mounting, never
inheritance logic in the storage layer. The canonical configuration
mirrors Claude Code: a user mount (durable facts about the user) plus
a project mount (facts local to the work). Memory always requires an
explicit scope — there are no silent isolation decisions.

## The six commands

One `memory` tool carries the whole vocabulary, on every transport:

| command | does |
|---|---|
| `view` | read a document (line-numbered) or list a directory; `/` renders the index |
| `create` | create **or overwrite** a document (an overwrite is reminded, not refused) |
| `str_replace` | replace exactly one occurrence; 0 or N matches fail correctively |
| `insert` | insert text after a line number |
| `delete` | delete a document |
| `rename` | move a document (cross-mount moves are composed, not atomic) |

The write discipline the shipped prompt pack teaches: check before you
create, update rather than duplicate, delete what proved wrong, route
user-durable facts to the user mount and project facts to the project
mount. Never store secrets, tokens, or credentials.

## Every write is a receipt

Every mutation appends a full-content version row — path, content,
actor (who wrote: a `conversation_id`, `cli:<host>`, `mcp:<host>`,
`eval:<scenario>`), tz-aware UTC timestamp. Point-in-time reads and
rollback come with the store; redaction clears content while
preserving the audit skeleton.

## Stores

- `FileStore(root)` — a plain directory; markdown + frontmatter
  documents, JSONL version sidecars. **One writer per root at a
  time** (see `neosian docs topology`).
- `PostgresStore(dsn)` — the `postgres` extra; multi-worker safe
  (optimistic concurrency). The DSN arrives only through the
  `NEOSIAN_POSTGRES_DSN` environment variable, never an argv flag.

Both implement the same `MemoryStore` ABC; a host may implement its
own, kept honest by the shipped `MemoryStoreContract` conformance kit.

## Four transports, one dispatcher

The same memory is served through the provider-agnostic function
tool, Anthropic's native `memory_20250818` declaration (a flag, same
execution), the MCP server (`neosian docs mcp`), and the shell
(`neosian docs cli`). The command vocabulary, mounts, read-only
enforcement, and corrective failures are identical on all four.
