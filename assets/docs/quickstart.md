---
title: "Start with your existing agent"
summary: Install the binary, connect an agent and read your first memory
---

# Start with your existing agent

Install the native binary on macOS or Linux:

```sh
curl -fsS https://neosian.com/install | sh
"$HOME/.neosian/bin/neosian" setup --write
"$HOME/.neosian/bin/neosian" setup --check
```

Use the installer's printed absolute path if you selected another directory.
It preserves existing commands, so a plain `neosian` may still run an older
installation. Use `neosian status` to inspect running, PATH and registered paths;
update using the installer or package manager that owns that installation.

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

`setup --check` inspects saved registrations without rewriting them and tests
the running binary through MCP initialization, tool discovery and a temporary
memory write/read. It removes that temporary store afterward. Add `--client C`
to check one client, repeat the same `--at` override if used, and add `--json`
for a structured report. `--check` cannot be combined with `--write`.
Storage, profile sources, required environment variable names and capture policy
are reported without opening the configured stores or printing credential values.
Exit 0 means these checks passed; exit 1 means a finding or probe needs attention.
PATH differences are advisory. Source connectivity and permissions, the client's
own environment, connection and hook delivery still need checking in that client.

Restart your agent after updating registrations, review its hook trust settings,
and confirm that `neosian-memory` connects. Its memory index points to
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
Use a portable profile to keep explicit source identity across machines:

```sh
neosian profile init work --write
neosian profile bind work --source workspace --root /absolute/path/to/knowledge --project "$PWD" --write
neosian setup --profile work --client codex
```

Profiles default to capture off; memory and selected skills still work.
Import/export carries the selection without local bindings or credentials.
Read `neosian docs profiles` to connect shared sources, opt into recording,
and enforce server permissions independently of the agent account. Existing
profile-free setups continue to record as described above.

FileStore needs no service. For Postgres or a remote state process, read
`neosian docs topology`. To implement an HTTP client or custom backend,
read `neosian docs wire` and `neosian docs stores`.
