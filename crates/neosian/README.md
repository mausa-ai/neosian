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
