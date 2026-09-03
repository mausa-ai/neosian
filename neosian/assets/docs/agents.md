---
title: Any agent — the record through hooks
summary: Claude Code, Codex and OpenCode hooks call neosian record; a span is one turn by the agent
---

# Any agent

neosian is the state layer for the agent you already use. The memory
half is the MCP server and `neosian mcp install` (`neosian docs mcp`).
The record half is `neosian record`: the agent's hooks call it with one
payload on stdin per event, and a prompt-to-stop span lands in the store
as **one turn with a foreign actor** — `claude-code:<session_id>` — in a
conversation keyed by the session id, plus a small **sessions document**
under the scope. Transcript files are never read: the documented hook
payloads are the import path.

## Install

```bash
neosian record install --client claude-code --root ~/.my-agent/state --scope user:me
neosian record install --client claude-code --url http://127.0.0.1:6367 --scope user:me --write
neosian record install --client codex --root ~/.my-agent/state --scope user:me --write
neosian record install --client opencode --root ~/.my-agent/state --scope user:me --write
```

- Print mode (the default) puts the paste-able `hooks` fragment on
  stdout — the one command on `UserPromptSubmit`, `PostToolUse` and
  `Stop` — and guidance on stderr.
- `--write` merges it into the project's `.claude/settings.json`
  (`.mcp.json`'s scope), preserving every other key, event and hook; a
  group carrying our command is replaced, so a re-run is idempotent.
  `~/.claude` must exist — neosian never creates another program's
  config home.
- The same store flags as `mcp install` render the same mount layout;
  the hook line adds `--spool` (absolute) and `--agent` when not the
  default. A DSN is never written into a hook line; `--url` carries the
  token through `NEOSIAN_CLIENT_TOKEN` in the client's own environment.
- Hooks beside an MCP server on one FileStore root are **two writers**:
  route both through the state process (`--url`) or Postgres.
- **Codex** takes the same fragment at the project's `.codex/hooks.json`
  (`~/.codex`, or `$CODEX_HOME`, must exist). Codex loads project hooks
  only for a trusted project, and reviews each new hook once in its
  `/hooks` command; automation that has vetted them runs
  `codex exec --dangerously-bypass-hook-trust`. The hook line carries
  `--agent codex`, so the writer is `codex:<thread_id>`; Codex's `Stop`
  hook expects JSON on stdout, and the verb answers `{}` there. The
  memory half is TOML Codex's own CLI writes: `neosian mcp install
  --client codex` prints the `[mcp_servers.neosian-memory]` table and
  the `codex mcp add …` line; `--write` is refused with that line as
  the fix.
- **OpenCode** has no shell hooks; it has a plugin API. The same command
  writes a small plugin file, `.opencode/plugins/neosian-record.js`
  (`~/.config/opencode`, or `$OPENCODE_CONFIG_DIR`, must exist), that
  maps `chat.message`, `tool.execute.after` and `session.idle` onto the
  verb's three payloads and pipes them in — ours whole, overwritten on
  re-run, never merged; print mode prints its source. The writer is
  `opencode:<session_id>`. The memory half is JSON: `neosian mcp install
  --client opencode` merges `{"mcp": {"neosian-memory": {"type":
  "local", …}}}` into the project's `opencode.json` (a `.jsonc` with
  comments is refused; paste the fragment). Any model OpenCode can run
  works, its free models included.

## What a span becomes

| hook | the record |
|---|---|
| `UserPromptSubmit` | the `USER` message that opens the turn (spooled) |
| `PostToolUse` | an `ASSISTANT` tool call + its `TOOL` result, the output kept to its head (4096 chars); a subagent's rounds (`agent_id`) are skipped |
| `Stop` | the final `ASSISTANT` text (`last_assistant_message`); the span lands as one `append_turn` by `claude-code:<session_id>`, and the sessions document is written by `claude-code:<session_id>#<turn>` |

The sessions document lives at `/memories/sessions/<session_id>` in the
first read-write mount — agent, conversation, started, last prompt,
turn count — so the next agent finds the listing in its index. Between
the prompt and the stop the span waits in a per-session spool
(`.neosian/spool` under the project; `--spool DIR`), never the store; a
failed landing keeps it, and the next stop carries the whole span.

Exit tiers bend once, for the hook's sake: Claude Code reads a hook's
exit 2 as "block", so the verb exits 2 only for a bad invocation
(caught at install time) and 1 for everything after — a broken store
never blocks the agent — and prints nothing on stdout unless `--json`
(the client injects a hook's stdout as context).

Read it back on any substrate:

```bash
neosian audit --scope user:me --conversation <session_id> --root ~/.my-agent/state
neosian audit --scope user:me --actor claude-code:<session_id> --url http://127.0.0.1:6367
```

## The client table

A row exists only while its walkthrough is green on a real install.

| client | memory (`mcp install`) | record (`record install`) |
|---|---|---|
| Claude Code | ✓ | ✓ — walkthrough green 2026-09-02 |
| Codex | ✓ print + `codex mcp add` | ✓ — walkthrough green 2026-09-03 (`codex exec`) |
| OpenCode | ✓ | ✓ — walkthrough green 2026-09-03 (`opencode run`, a plugin) |
| Claude Desktop | ✓ | no hooks surface |
| Cursor | ✓ | not yet — enters on a verified hook surface |
