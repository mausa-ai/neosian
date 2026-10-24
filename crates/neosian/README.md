# neosian

Memory and context for the agent you already use, as one Rust binary.
CLI, MCP and authenticated HTTP access to your files or Postgres; no model
account or Python runtime. This crate distributes the executable and has
no supported embedding SDK.

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
