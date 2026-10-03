---
title: "MCP: the same state, served to any client"
summary: memory, skills, recall and search over stdio; mcp install; McpServer consumes any server
---

# The MCP server

The same stores, served to any MCP client (Claude Code, Claude
Desktop, Cursor, Codex, OpenCode, Muse Code, Pi) over stdio; the MCP SDK ships in
the install and loads at first use. For MCP over the network, the state
process mounts this same factory's server at `/mcp`: `neosian serve`
with mounts (`neosian docs topology`).

## The state set

The memory server becomes the state server one tool at a time. Today it
serves seven:

- **`memory`**: the six commands over the mounts (`neosian docs
  memory`), the same definition the function tool carries.
- **`list_skills()`** and **`load_skill(name)`**: the skills kept as
  `skills/<name>` documents in the mounts (`neosian docs skills`),
  read-only; writing one is a `memory` call under the mount's flag.
  Every skill is also an MCP **prompt** (`prompts/list`,
  `prompts/get`), listed live, so a client that renders prompts as
  commands (Claude Code: `/mcp__neosian-memory__<name>`) gets a skill
  one agent wrote as a command in the next.
- **`recall_turn(turn, conversation)`**: one turn of a recorded
  conversation, verbatim: a hook-fed session of another agent
  (`neosian docs agents`), a neosian `Conversation`, anything the store
  holds. `conversation` is required here (the server has no
  conversation of its own) and any id the store holds is addressable:
  which conversations are shareable is the host's decision, never the
  library's. The ids are in the memory index under `sessions/` and in
  the "where we left off" block a `SessionStart` hook prints.
- **`search_history(query, conversation=None, limit=10)`**: the turns of
  any recorded conversation holding every term of `query` (whitespace-
  separated, case-insensitive substrings of the message text, the tool
  calls and their arguments, the tool results), newest first, at most
  50; `conversation` narrows to one. A hit is `[<id> #<turn>] <stamp>
  <actor>` and a snippet around the match, and the footer names the
  `recall_turn` call that re-reads it: search finds, recall opens. What
  is searchable is the host's decision, as with recall. A long tool
  result shows its head and tail; `recall_turn(..., call=<id>)`, the id
  the marker names, reads that one result whole.
- **`continue_session(conversation=None)`** (DESIGN §33): the one call
  an agent makes when the user says to continue where another session
  left off. It delivers that conversation whole within a budget (every
  user prompt and final answer verbatim, tool rounds as one line each,
  older turns as log lines), the pending handoff note first, and marks
  the note picked up. A bare call means the note's session, else the
  most recent session listed under `sessions/`; `conversation=<id>`
  names one. A session that calls it is linked: its sessions document
  says `continues: <id>`, and `neosian audit` shows it.
- **`handoff(note)`**: the note a departing agent writes for whoever
  continues next, in any agent: one baton per scope (`/project/handoff`),
  shown in full at the next session start until a session picks it up,
  never deleted (a newer note replaces it; versions keep the old). Under
  2048 characters: what was being done, what is done, what is next, what
  is open. The wheel's own `handoff` skill is served as a prompt
  (`/mcp__neosian-memory__handoff` in Claude Code) beside the mounts'
  skills, so the user can ask for the note with one command.

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
`NEOSIAN_POSTGRES_DSN`, never an argv flag. The server's
instructions depend on the client's start door (DESIGN §33), which the
server reads from the actor the installer rendered (`mcp:<client>`): a
client whose `SessionStart` hook prints the index (Claude Code, Codex,
Cursor, Muse Code, or Pi through its extension) gets the write discipline
alone. Pi's extension includes that discipline because its direct MCP
mode does not show server instructions. Any other client (OpenCode,
Claude Desktop, a hand-written entry) gets the memory index, the pending handoff note and "where we
left off" in the instructions, refreshed per connection. Every shipped
store keeps conversations too, so the state set is always on the list:
`recall_turn`, `search_history`, `continue_session` and `handoff`.

Hosts that embed the server in their own transport use
`create_memory_server` from `neosian.mcp`; passing their conversation
store as `conversations=` adds the state set beside `memory`, and
`session_start="hook"` says the host prints the start blocks itself.

## Register a client

`neosian mcp install` prints the exact registration for a client,
and applies it only when you ask:

```bash
neosian mcp install --client claude-code                 # once per machine: the home, no mount
neosian mcp install --client opencode --write
neosian mcp install --client claude-code --level project --scope user:me --write
```

- **Once per machine** (`--level user`, the default): the entry lands
  in the client's own config and names the home (`~/.neosian`, or
  `$NEOSIAN_HOME`) and no mount. The client spawns the server in the
  session's directory, and the server derives that session's layout
  there: `user:<login>` at `/user`, `user:<login>/proj:<slug>` at
  `/project` (`neosian docs agents`). Where the directory has no name
  to derive a project from (a desktop client spawns at `/`), the server
  serves `/user` alone. `--scope` and `--mount` are written into the
  line at either level.
- **`--level project`** writes this directory's file, with its
  two-mount layout spelled into the line. Claude Desktop, Cursor and
  Codex keep one file for every project, so they have the user level
  alone, and asking them for `--level project` is refused (exit 2).
- Print mode (the default) puts the paste-able fragment on stdout and
  the target path plus guidance on stderr.
- `--write` merges the entry into the client's config file, preserving
  every other key. Targets, user level then project level:
  `opencode` → `opencode.json` in its config directory
  (`~/.config/opencode`, or `$OPENCODE_CONFIG_DIR`), or the project's
  `opencode.json`, the entry under `mcp` in OpenCode's own shape
  (`type: local`, one `command` array; an `opencode.jsonc` holding plain
  JSON is used instead, while a commented one is preserved with a note);
  `claude-desktop` → its platform config file; `cursor` →
  `~/.cursor/mcp.json` (credential environment variables are forwarded as
  `${env:NAME}` references because Cursor clears custom MCP environment);
  `claude-code` at the project level → the project's `./.mcp.json`.
- `pi` (Pi 1.0.1+) uses `~/.pi/agent/mcp.json`, relocated by
  `PI_CODING_AGENT_DIR`, or `.pi/mcp.json` for a trusted project. The
  entry uses native `mcpServers` with direct tool exposure; tokens are
  inherited from the client environment. `setup --client pi --write`
  installs its recording and startup extension too (`neosian docs agents`).
- `muse-code` uses `$XDG_CONFIG_HOME/muse/settings.json` (default
  `~/.config/muse/settings.json`) at user level, or the shared `.mcp.json`
  at project level. New user settings include `schema_version: 1`.
  Credential variables are forwarded by `${NAME}` references in the
  server's `env`, since Muse clears its child environment. The hooks
  half needs managed hooks for credentials (`neosian docs agents`).
- **A file the client's own CLI writes is print-only.** Claude Code's
  user scope lives in `~/.claude.json` (under `$CLAUDE_CONFIG_DIR` when
  set), which Claude Code rewrites as it runs, and Codex's
  `~/.codex/config.toml` is TOML: stdout is the fragment (JSON, or the
  `[mcp_servers.neosian-memory]` table), stderr the line that applies
  it (`claude mcp add-json --scope user …`, `codex mcp add …`), and
  `--write` is refused with that line. `neosian setup --write` runs it
  for you when the client's binary is on PATH. Both lines are POSIX
  shell quoting; `setup` itself uses no shell.
- A project's entry shadows the user's (a client connects a name once,
  from the nearest level), so a user-level `--write` removes this
  directory's old entry, ours only, and says so. Muse preserves the shared
  `.mcp.json` entry and reports `mcp_shadowed_by` instead: Claude Code
  reads that same file.
- A client whose config directory does not exist is refused (exit 1):
  neosian never creates another program's config home. Install the
  client first.
- The registration embeds the resolved settings: an absolute `--root`,
  any mounts you named in canonical `--mount` form, and the current
  interpreter's absolute path (GUI clients do not inherit your shell's
  PATH). A Postgres registration never contains the DSN: set
  `NEOSIAN_POSTGRES_DSN` in the client's own environment.
- `--json` prints one machine-readable envelope instead.

The record half, a foreign agent's hooks, is `neosian record install`,
which takes the same flags and the same level (`neosian docs agents`).

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
  spawns the server (`env` is merged over a filtered inheritance, so
  pass a secret the server needs explicitly); `McpServer.http(url,
  headers=)` reaches a streamable-HTTP endpoint (the state process's
  `/mcp` with a bearer header, or any remote); `McpServer.in_process(
  server)` connects an SDK server object with no socket: the keyless
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
  after it fails in-band, naming the server. Only connecting raises
  `McpConnectionError` (`tool_mcp_connection_failed`), for a command
  that cannot spawn, a failed handshake, an unreachable URL.
- **Everything else applies unchanged.** The approval gate, hooks,
  `Conversation` (pass the tools in the base config), link handles in
  tool arguments: a bridged tool is an ordinary tool.
- **The shell does the same.** `[[chat.mcp]]` tables in `config.toml`
  (`neosian docs cli`) name servers `neosian chat` opens for a session,
  their tools added, `prefix` for a name chat already has.

## Cooperating local writers

MCP servers, hooks, shell commands and embedding applications can share a
local FileStore root. Upgraded writers coordinate through its OS lock;
restart every writer when upgrading. Postgres supports distributed workers,
and the state process (`neosian serve`) provides remote access to either
backend. `neosian setup --url URL --write --yes` moves every detected client
to that process in one run. Omit `--yes` on a terminal to select clients first.
See `neosian docs topology` for the storage and coordination guarantees.

## Durable messages and reminders

See `neosian docs messaging` for the shared `messages` tool, conversation and
scope destinations, history annotations, explicit acknowledgment and claims.
Reminders can be snoozed with a finding and a new due time on the same ID.
Native conversations opt in with `mailbox=MailboxConfig()` from
`neosian.messaging`. Regenerate client adapters after upgrading for automatic
delivery. Pi and OpenCode also support wake requests while attached.
