---
title: "Start with your existing agent"
summary: Install the binary, connect an agent and read your first memory
---

# Start with your existing agent

Install the native binary on macOS or Linux:

```sh
curl -fsS https://neosian.com/install | sh
neosian setup --write
neosian status
```

Windows installers and MSI are on the GitHub Release. Package manager
alternatives are `npm install -g neosian` and `cargo install neosian --locked`.
Cargo builds from source with the pinned Rust toolchain. There is no Python
runtime or model-provider account requirement. See `neosian docs migration`
when upgrading from v1.

Setup connects Claude Code, Codex, OpenCode, Muse Code, Pi and interactive
Cursor through MCP and hooks. It registers the executable running it;
`--binary PATH` selects an explicit binary. Inspect its preview before
`--write`; `--write --yes` connects all detected clients in scripts.
`--at CLIENT=DIR` overrides a client directory for that invocation.

Restart your agent after updating registrations. Its memory index points to
small documents under mounted scopes. The `memory` tool reads and edits them;
hooks record conversations and supply startup context. The `docs` tool reads
these same versioned pages. `list_skills` and `load_skill` expose reusable
instructions, including `handoff` and `upkeep`.

Try the same operations directly:

```sh
neosian memory create /memories/decision --scope user:demo --content 'Keep the decision and its reason.'
neosian memory view /memories/decision --scope user:demo
neosian memory versions /memories/decision --scope user:demo --json
```

The default home is `~/.neosian`; `NEOSIAN_HOME` or `--root DIR` selects
another. `--scope` mounts one scope at `/memories`. Without explicit mounts,
the local username and project directory derive `/user` and `/project`.
Those defaults are local conveniences, not portable cross-machine identity.
Use explicit scopes to share known state.

FileStore needs no service. For Postgres or a remote state process, read
`neosian docs topology`. To implement an HTTP client or custom backend,
read `neosian docs wire` and `neosian docs stores`.
