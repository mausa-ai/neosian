---
title: "MCP: the same state, served to any client"
summary: memory, skills and recall_turn over stdio; mcp install; McpServer consumes any server
---

# The MCP server

The same stores, served to any MCP client — Claude Code, Claude
Desktop, Cursor, Codex, OpenCode — over stdio; the MCP SDK ships in
the install and loads at first use. For MCP over the network, the state
process mounts this same factory's server at `/mcp`: `neosian serve`
with mounts (`neosian docs topology`).

## The state set

The memory server becomes the state server one tool at a time. Today it
serves four:

- **`memory`** — the six commands over the mounts (`neosian docs
  memory`), the same definition the function tool carries.
- **`list_skills()`** and **`load_skill(name)`** — the skills kept as
  `skills/<name>` documents in the mounts (`neosian docs skills`),
  read-only; writing one is a `memory` call under the mount's flag.
  Every skill is also an MCP **prompt** (`prompts/list`,
  `prompts/get`), listed live — a client that renders prompts as
  commands (Claude Code: `/mcp__neosian-memory__<name>`) gets a skill
  one agent wrote as a command in the next.
- **`recall_turn(turn, conversation)`** — one turn of a recorded
  conversation, verbatim: a hook-fed session of another agent
  (`neosian docs agents`), a neosian `Conversation`, anything the store
  holds. `conversation` is required here — the server has no
  conversation of its own — and any id the store holds is addressable:
  which conversations are shareable is the host's decision, never the
  library's. The ids are in the memory index under `sessions/` and in
  the "where we left off" block a `SessionStart` hook prints.

## Serve

```bash
python -m neosian.mcp --scope user:me                  # the home, ~/.neosian
python -m neosian.mcp --root ~/.my-agent/memory --scope user:me
```

The store flags are the same grammar as `neosian memory`: `--scope`
is the single-mount sugar, `--mount scope=...,path=...` is repeatable
(append `,ro` for read-only or `,eo` for edit-only), `--actor`
defaults to `mcp`
(convention `mcp:<host>`), and Postgres arrives only through
`NEOSIAN_POSTGRES_DSN` — never an argv flag. The server's
instructions carry the same memory index and prompt pack the function
tool uses; the index refreshes per connection. Every shipped store
keeps conversations too, so `recall_turn` is always on the list.

Hosts that embed the server in their own transport use
`create_memory_server` from `neosian.mcp`; passing their conversation
store as `conversations=` adds `recall_turn` beside `memory`.

## Register a client

`neosian mcp install` prints the exact registration for a client —
and applies it only when you ask:

```bash
neosian mcp install --client claude-code                 # the home, this project's layout
neosian mcp install --client claude-code --root ~/.my-agent/memory --scope user:me
neosian mcp install --client claude-desktop ... --write
```

- Print mode (the default) puts the paste-able `mcpServers` JSON
  fragment on stdout and the target path plus guidance on stderr.
- `--write` merges the entry into the client's config file,
  preserving every other key. Targets: `claude-code` → the project's
  `./.mcp.json`; `claude-desktop` → its platform config file;
  `cursor` → `~/.cursor/mcp.json`; `codex` → `~/.codex/config.toml`
  (TOML, print-only: stdout is the `[mcp_servers.neosian-memory]`
  table and stderr the `codex mcp add …` line that applies it —
  Codex's own CLI is the writer, so `--write` is refused); `opencode` →
  the project's `opencode.json`, the entry under `mcp` in OpenCode's own
  shape (`type: local`, one `command` array).
- A client whose config directory does not exist is refused (exit 1)
  — neosian never creates another program's config home. Install the
  client first.
- With no flags the registration names the home (`~/.neosian`, or
  `$NEOSIAN_HOME`) and this directory's two-mount layout —
  `user:<login>` at `/user`, `user:<login>/proj:<slug>` at `/project` —
  spelled out so the scope stays explicit (`neosian docs agents`).
- The registration embeds the resolved settings: an absolute `--root`
  (clients spawn servers from arbitrary directories), mounts in
  canonical `--mount` form, and the current interpreter's absolute
  path (GUI clients do not inherit your shell's PATH). A Postgres
  registration never contains the DSN — set `NEOSIAN_POSTGRES_DSN` in
  the client's own environment.
- `--json` prints one machine-readable envelope instead.

The record half — a foreign agent's hooks — is `neosian record install`,
which renders the same mount layout from the same flags
(`neosian docs agents`).

## Consume a server

The other direction, the same SDK: a neosian agent uses any MCP
server's tools. `McpServer` connects through the official client for
the lifetime of an `async with`, lists the tools once, and hands them
over as plain tool functions:

```python
from neosian import Agent, AgentConfig
from neosian.mcp import McpServer

async with McpServer.stdio(
    "python", ["-m", "neosian.mcp", "--scope", "user:me"]
) as server:
    agent = Agent(
        AgentConfig(
            system_prompt="You remember things.",
            tools=[*server.tools],
        )
    )
    response = await agent.run(messages, stream=False)
```

- **Three endpoints.** `McpServer.stdio(command, args, env=, cwd=)`
  spawns the server (`env` is merged over a filtered inheritance —
  pass a secret the server needs explicitly); `McpServer.http(url,
  headers=)` reaches a streamable-HTTP endpoint (the state process's
  `/mcp` with a bearer header, or any remote); `McpServer.in_process(
  server)` connects an SDK server object with no socket — the keyless
  door, `create_memory_server`'s result included.
- **The schema is the server's.** Each tool's `inputSchema` reaches
  the model verbatim; arguments cross unchecked and the server answers
  a bad one in-band. `is_error` comes back as a failed `ToolResult`;
  text blocks join, an image, audio or resource block leaves a one-line
  marker (type, media type, size) rather than vanishing;
  `structuredContent` is the data when the server sends one.
- **Names.** Tools keep the names the server declares. Two servers that
  clash take `prefix="gh"` → `gh__search`; an unprefixed collision is
  refused when the `Agent` is built, the message naming the server.
- **Lifetime.** The tools are live inside the `async with`; a call
  after it fails in-band, naming the server. Only connecting raises —
  `McpConnectionError` (`tool_mcp_connection_failed`), for a command
  that cannot spawn, a failed handshake, an unreachable URL.
- **Everything else applies unchanged.** The approval gate, hooks,
  `Conversation` (pass the tools in the base config), link handles in
  tool arguments — a bridged tool is an ordinary tool.

## One writer per root

An MCP server serving a FileStore root **owns** that root while it
runs. Do not write to the same root with `neosian memory` or an
embedding application at the same time — reads are fine, concurrent
writers are not arbitrated on files. The home is one root for every
project, so two projects' servers on it are two writers. Multi-writer
needs route to `PostgresStore` or to the state process (`neosian
serve`, no flags), where one process owns the root for every client. The full rule:
`neosian docs topology`.
