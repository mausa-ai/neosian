# neosian

Memory and context for the agent you already use, as one Rust binary.
CLI, MCP and authenticated HTTP access to your files or Postgres; no model
account or Python runtime. This crate distributes the executable and has
no supported embedding SDK.

Portable workspace profiles select the same authorized memory and skills
across machines. Local bindings keep credentials out of profile exports;
new profiles capture no conversations until explicitly enabled. Read
`neosian docs profiles` for context selection, grants and capture policy.

`neosian memory search QUERY` and MCP `search_memory` find current permitted
documents and selected metadata with bounded excerpts and source/version
references. Files, Postgres and capable remote services share the same
keyword semantics. Search requires no model account or derived index.

```sh
cargo install neosian --locked
neosian setup --write
neosian status
neosian docs
```

[Documentation](https://neosian.com/docs) ·
[Source](https://github.com/mausa-ai/neosian) ·
[Migration from v1](https://neosian.com/docs/migration)

The exact toolchain is pinned by `rust-toolchain.toml`; Cargo's workspace
version is authoritative. Data format 1, storage wire 6, Postgres schema 4.

`neosian knowledge import` previews Markdown or knowledge-bundle ingestion
into an explicit mounted collection. Optional field rules, deliberate references
and source provenance stay in readable versioned content. Validate and export
that knowledge across supported backends; retain full histories through the
separate archive commands. See `neosian docs knowledge`.

`neosian skill` manages portable, versioned instructions with optional text
resources, dependencies and profile revision guards. Read `neosian docs skills`
for previews, exact update/recovery semantics and permission boundaries.

`neosian upkeep scan/prepare/apply` and MCP upkeep tools provide bounded,
reviewed memory corrections with source provenance, current-state guards and
explicit approval. Apply requires paused writers; inspect interrupted batches
before preparing remaining work. Read `neosian docs memory` for the workflow.

`neosian knowledge govern/read` adds review assertions and explicit retrieval
policies for shared procedures and decisions. Review changes use upkeep proposals;
profile policies select effective knowledge and skills without granting access.
Read `neosian docs governance` for content hashes, expiry and erasure.

`neosian changes read` and MCP `changes` expose bounded, durable per-scope
references. File memory mutations recover through a redo journal; Postgres uses
transactions with its optional operator-installed changes extension. Hosts handle
reconciliation and can attach references to mailbox work. Read `neosian docs
changes` for cursors, gaps, current authority and upgrade requirements.

`neosian exchange` exports current-memory snapshots and deltas from an authorized
tracked scope to an explicit local replica. Atomic apply checkpoints, conflict
refusal and erasure protect retained observations. Pause source writers; raw
history and bindings do not travel. Read `neosian docs exchange` for bounds.
