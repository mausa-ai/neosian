---
title: "Any agent: the record through hooks"
summary: Claude Code, Codex and OpenCode hooks call neosian record; SessionStart reads it back
---

# Any agent

neosian is the state layer for the agent you already use. The memory
half is the MCP server and `neosian mcp install` (`neosian docs mcp`).
The record half is `neosian record`: the agent's hooks call it with one
payload on stdin per event, and a prompt-to-stop span lands in the store
as **one turn with a foreign actor** (`claude-code:<session_id>`) in a
conversation keyed by the session id, plus a small **sessions document**
under the scope. Transcript files are never read: the documented hook
payloads are the import path.

## Install

```bash
neosian setup --write                                             # every client found, once per machine
neosian setup --url http://127.0.0.1:6367 --write                 # the same, behind the state process
neosian record install --client claude-code                       # print the user-level hooks
neosian record install --client codex --write
neosian record install --client opencode --level project --scope user:me --write
```

- **`neosian setup`** runs this installer and `mcp install` for every
  client it finds (their config directories are the evidence), prints
  first and applies with `--write`; `neosian status` shows each client
  green afterwards, at which level, and names an interpreter that
  stopped resolving.
- **Once per machine.** By default the hooks land in the client's own
  settings (`--level user`) and serve every project: with no store flag
  the line names `~/.neosian` (or `$NEOSIAN_HOME`) and no mount, and the
  verb derives each session's layout where it runs: `user:<login>` at
  `/user`, `user:<login>/proj:<slug>` at `/project`, the slug the
  project directory's name. Nothing is written into a project. A
  neosian agent lands in the same place with `FileStore(home())` and
  `project_scope()` (`neosian docs quickstart`).
- **`--level project`** writes this directory's file instead, with its
  layout spelled into the line: a scope of your own for one project
  (`--scope`, `--mount`), or recording only where you ask for it. A
  lighter per-project override needs no second registration: set
  `NEOSIAN_SCOPE` in that project's environment for the client. Muse clears
  this variable at launch; name its scope explicitly when installing.
- **One level per client.** Every client merges its hook sources, so
  ours at two levels would run twice and land each span twice. A
  user-level `--write` removes this directory's project-level entry
  (ours only, by its command; every other hook and key survives) and
  says so; a project-level install beside user-level hooks is refused.
  A project that still carries hooks from an earlier install shows
  `level both` in `neosian status`, with the fix: run `neosian setup
  --write` there. Inside a repository that commits those hooks, that
  edits the committed file.
- **Which project.** Claude Code runs a hook in a working directory
  that moves when the agent runs `cd`, so its line ends on `--project
  "$CLAUDE_PROJECT_DIR"`, the directory the session started in, which
  stays put. Codex runs hooks in the session's directory and needs
  nothing; the OpenCode plugin passes the directory it was opened with.
  A directory with no name to derive a project from (`/`) records to
  `/user` alone; a hook never exits 2, which Claude Code reads as
  "block the prompt".
- Print mode (the default) puts the paste-able `hooks` fragment on
  stdout (Muse shows its multi-file change preview instead; the one command on `UserPromptSubmit`, `PostToolUse`, `Stop`
  and `SessionStart`) and guidance on stderr.
- `--write` merges it into `~/.claude/settings.json` (under
  `$CLAUDE_CONFIG_DIR` when set), preserving every other key, event and
  hook; a group carrying our command is replaced, so a re-run is
  idempotent. `~/.claude` must exist: neosian never creates another
  program's config home.
- The store flags are `mcp install`'s; the hook line adds `--spool`
  (absolute) and `--agent` when not the default. A DSN is never written
  into a hook line; `--url` carries the token through
  `NEOSIAN_CLIENT_TOKEN` in the client's own environment, which is now
  a setting for the machine, not for one project.
- Hooks beside an MCP server on one FileStore root are **two writers**,
  and the home is one root for every project on the machine: run
  `neosian serve` (no flags: it serves the home; `neosian docs
  topology` has the per-user service recipe), then `neosian setup --url
  URL --write` moves every client there in one run. Or use Postgres.
- **Codex** takes the same fragment at `~/.codex/hooks.json` (`~/.codex`,
  or `$CODEX_HOME`, must exist). At the user level there is no project
  trust step, but Codex runs a new hook only once you have reviewed it
  in its `/hooks` command; automation that has vetted them runs `codex
  exec --dangerously-bypass-hook-trust`. At `--level project` it is the
  project's `.codex/hooks.json`, loaded only for a trusted project. The
  hook line carries `--agent codex`, so the writer is
  `codex:<thread_id>`; Codex's `Stop` hook expects JSON on stdout, and
  the verb answers `{}` there. The memory half is TOML Codex's own CLI
  writes: `neosian mcp install --client codex` prints the
  `[mcp_servers.neosian-memory]` table and the `codex mcp add …` line,
  and `neosian setup --write` runs that line for you.
- **OpenCode** has no shell hooks; it has a plugin API. The same command
  writes a small plugin file, `plugins/neosian-record.js` in OpenCode's
  own config directory (`~/.config/opencode`, or `$OPENCODE_CONFIG_DIR`,
  which must exist; `.opencode/plugins/` in the project at `--level
  project`), that maps `chat.message`, `tool.execute.after` and
  `session.idle` onto the verb's three write payloads and pipes them in:
  ours whole, overwritten on re-run, never merged; print mode prints
  its source. The writer is `opencode:<session_id>`. OpenCode's only
  context door is an experimental per-call hook, so its row is
  write-only: the read side below is not wired there. The memory half
  is JSON: `neosian mcp install --client opencode` merges `{"mcp":
  {"neosian-memory": {"type": "local", …}}}` into `opencode.json` in the
  same directory (an `opencode.jsonc` beside it is refused, since
  comments do not survive a merge; paste the fragment). Any model
  OpenCode can run works, its free models included.

## What a span becomes

| hook | the record |
|---|---|
| `UserPromptSubmit` | the `USER` message that opens the turn (spooled) |
| `PostToolUse` | an `ASSISTANT` tool call + its `TOOL` result, the output kept to its head (4096 chars); a subagent's rounds (`agent_id`) are skipped |
| `Stop` | the final `ASSISTANT` text (`last_assistant_message`); the span lands as one `append_turn` by `claude-code:<session_id>`, and the sessions document is written by `claude-code:<session_id>#<turn>` |
| `SessionStart` | nothing written: the verb prints the context (below), which the client adds to the model's window |

The sessions document lives at `sessions/<session_id>` in the mount at
`/project` (else the first read-write mount) with agent, conversation,
started, last prompt and turn count, so the next agent finds the listing
in its index. Between the prompt and the stop the span waits in a
per-session spool (`spool/` under the home; `--spool DIR`), never the store; a
failed landing keeps it, and the next stop carries the whole span.

Exit tiers bend once, for the hook's sake: Claude Code reads a hook's
exit 2 as "block", so the verb exits 2 only for a bad invocation
(caught at install time) and 1 for everything after (a broken store
never blocks the agent) and prints nothing on stdout unless `--json`,
except on `SessionStart`.

## Session start: where we left off

Claude Code and Codex add a `SessionStart` hook's stdout to the model's
context, so on that event the verb prints two blocks and writes
nothing:

1. **The memory index** of the scope: the same rendering the MCP
   server's instructions carry, so the sessions documents are listed.
2. **Where we left off**: the scope's recent sessions, log-projected
   the way a conversation view is (`neosian docs memory`): one line per
   turn, its number in square brackets, newest session first, under one
   8192-character budget shared evenly (older turns fold into a count
   line: paging, never deletion). Which sessions depends on `source`:
   after a **compaction** (`source: compact`) the session's own record
   comes back, the re-injection of what the client just paged out;
   on `startup`, `resume`, `clear` or `fork` the three most recently
   written sessions, the own one included when it is among them.

The block names every turn it shows, and the footer names the call
that re-reads one: `recall_turn(n, conversation="<id>")` on the
`neosian-memory` MCP server (`neosian docs mcp`), the same tool a
neosian `Conversation` uses to page its own history. Bodies stay
behind tools; the block is a table of contents, not the transcript. An
empty scope still prints the frame, so the agent knows the door exists.
A store that cannot be reached exits 1 with nothing on stdout:
`SessionStart` never blocks.

The window stays each agent's own: neosian feeds it at the client's
extension points (session start, the post-compaction re-injection)
and never replaces it. The three lifetimes hold across clients
(`neosian docs memory`): skills are how (the `skills/` documents of
the mounts, the same list and the same MCP prompts from every client,
`neosian docs skills`), memory is what we know, the board is what we
are doing now, and the record is history, recallable
turn by turn, never in the window whole. Reflection at pre-compact is not offered: a hook
process is keyless, and the foreign agent's own model is the only one
in the room.

Read it back on any substrate:

```bash
neosian audit --scope "$(python -c 'import neosian; print(neosian.project_scope())')"
neosian audit --scope user:me --conversation <session_id> --root ~/.my-agent/state
neosian audit --scope user:me --actor claude-code:<session_id> --url http://127.0.0.1:6367
```

## Muse Code

`neosian setup --client muse-code --write` installs both halves. Muse's
configuration directory must already exist: `$XDG_CONFIG_HOME/muse`, or
`~/.config/muse`. Its user `settings.json` holds both `mcpServers` and
`hooks`; a new file includes `schema_version: 1`. Existing malformed or
unsupported settings are refused, and other settings survive a merge.
The writer is `muse-code:<session_id>`.

FileStore hooks use this user settings file, or `.muse/hooks.json` with
`--level project`. Project hooks require a trusted workspace, such as
`muse exec --trust-workspace`. Muse runs lifecycle hooks in the session's
workspace and tool hooks in the tool's effective directory; the stop
lands the shared spool in the session's scope. Startup context is plain
text, capped below Muse's 16 KiB stdout limit without splitting UTF-8.

Muse clears the environment of hooks and MCP processes. For `--url` or
Postgres, the installer therefore uses **user-level managed hooks**:
`neosian-hooks.json` beside settings, named by `managed_hooks_path`.
`managed_hooks_env_vars` gains only `NEOSIAN_CLIENT_TOKEN` or
`NEOSIAN_POSTGRES_DSN`. MCP entries reference the same variable with
`${NAME}`; values are never written to a registration or printed.
Credential-backed project hooks are refused: use `--level user`.
An existing managed pointer to another file is preserved and automatic
installation is refused with a manual merge explanation.

Switching stores removes only neosian's displaced hook groups. `status`
reads ordinary, managed and project hooks, and reports duplicates.
Muse hook print mode shows a path-keyed change preview: `merge`,
`remove_neosian_hooks`, and `add_managed_hooks_env_vars` name the edits;
it does not print unrelated settings. `setup` validates both halves
before writing either. Its JSON hook envelope includes this `files` map.

Muse and Claude Code share the project MCP file `.mcp.json`. Installing
Muse at user level preserves its existing neosian entry, which overrides
Muse's user registration. Both the installer and `status` report its
path as `mcp_shadowed_by`; change the shared entry deliberately when
moving stores.

References: Muse's [hooks](https://meta-models.github.io/muse-code-sdk/next/guides/extend/hooks/)
and [MCP configuration](https://meta-models.github.io/muse-code-sdk/next/guides/extend/mcp-servers/),
verified against Muse Code 1.3.0.

## The client table

A row exists only while its walkthrough is green on a real install. "Per
machine" is the default registration: one run of `neosian setup --write`,
two project directories, each session in its own `proj:` scope.

| client | memory (`mcp install`) | record (`record install`) | per machine | session start |
|---|---|---|---|---|
| Claude Code | ✓ user scope through `claude mcp add-json` | ✓ walkthrough green 2026-09-02 | ✓ 2026-09-19 (2.1.278): the server is spawned in the session's directory, the hook line's project directory expands | ✓ `SessionStart`, stdout as context (2026-09-03) |
| Codex | ✓ through `codex mcp add` | ✓ walkthrough green 2026-09-03 (`codex exec`) | ✓ 2026-09-19 (0.154.0): the same, user-level hooks with no project trust step | ✓ the same event and `source` values (its reference, 2026-09-03) |
| OpenCode | ✓ | ✓ walkthrough green 2026-09-03 (`opencode run`, a plugin) | ✓ 2026-09-19 (1.18.30, a free model): the same, the plugin passing the directory it was opened with | — (an experimental per-call door only; not wired) |
| Claude Desktop | ✓ | no hooks surface | one file by nature; no project, so `/user` alone | — |
| Cursor | ✓ | not yet: its hooks are read (their own payload shape, a mapping of its own); next | | — |
| Muse Code | ✓ user settings or shared project `.mcp.json` | ✓ walkthrough green 2026-09-22 (`muse exec`, 1.3.0) | ✓ two projects, one user registration; authenticated managed hooks and MCP on the state process | ✓ `SessionStart`, plain stdout; prior turn recalled over MCP |
