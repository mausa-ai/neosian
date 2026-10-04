---
title: "The shell: one CLI for humans and agents"
summary: status, setup, chat, search, audit, eval; the memory grammar, --json, exit tiers
---

# The shell

One `neosian` for two readers: the operator console for the state your
agents already write, and the agent-facing grammar underneath it:
every verb with `--json` and exit tiers, so a script or an agent uses
the same doors. On a terminal the verbs render (a table, a tree,
markdown). `NO_COLOR` removes color while keeping terminal layouts readable;
under a pipe or `--json`, the existing output is unchanged. Reports use
section headings and lightly ruled tables; below 80 columns, wide tables
become labeled records. Paths, identifiers and findings wrap without being
cut off or interpreted as formatting.

`status` groups installation details, provider keys and their sources,
scopes, clients, the last session and actionable findings. Setup results,
`configure --list`, version history, maintenance and store transfers use
the same presentation. One-shot chat answers render Markdown on a terminal;
streamed answers remain immediate, with tool events and write receipts
shown as they arrive. Document bodies, continued transcripts, configuration
previews and protocol output retain their original content.

## Set up and talk

```
neosian status [--json]                  # is this machine set up?
neosian setup [--client C]… [--at C=DIR]… [--write] [--yes]    # wire the installed agents to this store
neosian configure [--list | --provider NAME --key - | --env NAME --key - | --delete]
neosian chat [PROMPT] [--model M] [--agent FILE] [--resume ID] [--json]
neosian update [--check | --write] [--mode off|notify|auto]
neosian                                  # on a terminal: chat; under a pipe: the help
```

- **`status`**: the home and whether it exists; the config and which
  providers have a key (names and sources, never values); this
  directory's two scopes; per client (Claude Code, Codex, OpenCode, Muse Code, Cursor, Pi)
  installed / MCP registered / hooks present / at which level (`user`,
  `project`, or `both`) / the interpreter those files name still
  resolving; message delivery capabilities; the last recorded session;
  and a note when a client carries the hooks at both levels (they would
  run twice);
  the installation shape with its upgrade line; the update knob. Exit 0
  whenever it ran; findings are data. For Muse, managed hooks count as
  user level; `mcp_shadowed_by` names a preserved shared project MCP entry
  that overrides the user registration.
- **`setup`**: detects the clients present and runs both installers
  for each (`mcp install` and `record install`), once per machine:
  each client's own config, the home, no mount (`--level project` for
  this directory's files). It prints what would land; `--write` applies
  the selected clients. A terminal write without `--client` presents a
  checklist, all detected clients selected initially. Numbers toggle; Enter
  confirms; `q` or EOF cancels without writing. `--yes` selects all detected
  clients without asking; repeated `--client C` selects explicitly.
  Print mode and `--json` never prompt. Unattended writes, including JSON,
  require `--yes` or explicit clients. Selections are never saved. It runs
  the client's own CLI for a file that CLI owns (`claude
  mcp add-json`, `codex mcp add`) when it is on PATH, printing the line
  otherwise (exit 1: something is left to do). A `note` line under a
  client says what the client itself still asks of the user (Codex
  reviews a new hook once in its `/hooks` command before it runs) or
  what stands beside a file written (OpenCode's commented
  `opencode.jsonc`, merged by OpenCode with the `opencode.json` the
  entry landed in). `--root` and `--url` reach both installers:
  `neosian setup --url URL --write` moves every client on the machine
  to the state process in one run after selection.
- **Client locations:** setup and status accept repeated `--at CLIENT=DIR`.
  DIR is the client's user configuration directory, not a project root or
  config filename; relative paths resolve from the current directory.
  An explicit directory overrides that client's normal environment/default
  location for this invocation only. Project-level files still belong to
  the current project. Missing client homes are refused, never created.
  Both reports name searched directories; JSON uses setup `searched` rows
  and status `searched_directory` per client. For example:
  `neosian setup --client pi --at pi=/opt/pi/agent --write`; inspect it with
  `neosian status --at pi=/opt/pi/agent`.
- **`configure`**: keys under `<home>/config.toml`, one row per
  provider the catalog knows (shipped, door rows, registered doors);
  `--provider NAME --key -` reads the key from stdin, never argv; `--env
  NAME --key -` names a door by the env var its key lives in, for one the
  shell has not loaded (an agent file registers it); bare on a terminal
  prompts for each. Every stored key reaches the environment before a
  model is built, a door registered later included.
- **`chat`**: the resident agent. It knows neosian (the `docs` tool
  reads the shipped pages on demand), writes to `/user` and `/project`
  on the home, loads the skills your other agents wrote, and runs the
  verbs on this page for you (the `neosian` tool, below). A PROMPT or
  piped stdin runs one turn; `--json` the envelope (text, tool calls,
  usage, µ$), refused with no turn (exit 2) since a session cannot print
  one object; `--model fake` is keyless. The model: `--model`, else
  `[chat] model` in `config.toml`, else the latest Sonnet. This is the
  resident agent's current choice, separate from library defaults.
  Explicit model IDs and selectors (`anthropic:sonnet:latest`) are accepted.
  Missing `ANTHROPIC_API_KEY` fails before inference; it never silently
  selects a different provider.
- **`update`**: checks PyPI's simple index; `[update] mode` is `off`
  (default), `notify` (one stderr line on the human verbs, once per
  24 h) or `auto` (applies a uv tool install within the major, never a
  pre-release over a stable, then re-executes itself).

**The session.** On a terminal `chat` (and bare `neosian`, and
`playground`) opens a full-screen session in the terminal's own colors:
the reply streams as Markdown, and a tool call is one line with a
one-line result until you expand it (click it, or ctrl+o for every
call), so a large result costs nothing until it is asked for. Tab and
shift+tab focus individual calls and the prompt; enter or space folds
the focused call. A border marks focus, including with `NO_COLOR`.
At the prompt, enter sends and ctrl+j breaks the line; up and down walk
what you sent; a sent message is a full highlighted line. Esc returns
to the prompt and interrupts a running turn, which then saves nothing;
ctrl+c copies a selection, else interrupts, else clears the prompt,
else asks for a second ctrl+c to leave; ctrl+d and `/exit` leave at
once; page up and page down scroll. The `--resume` line stays in the terminal after the
screen is restored. A PROMPT, a pipe and `--json` never open it.

**Seven commands.** A line that begins with `/` is a command, and a
command exists only where asking the agent is impossible or wrong: a
secret the model must never see, and the session itself. `/help` lists
them and the keys. `/configure` picks a provider and takes its API key
in a masked input: the key goes to `config.toml` as `configure --key -`
would store it, and never to the transcript, a turn or the model.
`/model` switches the model on the same conversation (a list of the
models a key opens; `/model ID` names one). The choice lasts for this
session; it does not change `[chat] model` in `config.toml`. `/resume`
continues an earlier session of this chat in this project (a list,
newest first; `/resume ID` names one) and draws its last ten turns again, as
`--resume` does at the start. `/new` starts a fresh conversation,
`/compact` folds the older turns now, `/exit` leaves. Typing `/` opens
them as a menu above the prompt that narrows as you type: up and down
choose, tab completes the choice so an argument can follow, enter runs
it, esc puts the menu away. Everything else is a sentence to the agent.

**The agent runs the verbs.** Ask "is this machine set up, any
reminders waiting?" and the resident agent runs `status` and reads its
report, through one tool, `neosian`, that takes the arguments you would
type. A form that only reads runs at once: `status`, `docs`, `version`,
`search`, `audit`, `continue`, `memory view` and `versions`, `messages
list` and `view`, `setup` without `--write`, `configure --list`. Every
other form (`setup --write`, `update`, `export`, `import`, a memory or
messages write, `configure --delete`) waits for your `y` in the session;
`n` or esc declines it and the agent is told. In a one-shot turn there
is no one to ask, so the form is declined and the agent names the
command for you to run. `chat`, `playground`, `eval`, `mcp`, `record`
and `serve` never run inside a chat, and a key never passes through the
model: `configure` stays yours to run.

**Chat's MCP servers.** `[[chat.mcp]]` tables in `config.toml` name the
servers `chat` opens for the session (one turn or many) and adds as
tools, each mirroring `McpServer` (`neosian docs mcp`): `name`, then
`command` + `args` + `env` (a subprocess) or `url` + `headers`
(streamable HTTP), and `prefix` for names that clash. Values are
literal: the file is 0600.

```toml
[[chat.mcp]]
name = "github"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = { GITHUB_TOKEN = "ghp_..." }
prefix = "gh"                        # gh__search_issues, ...

[[chat.mcp]]
name = "state"
url = "http://127.0.0.1:8765/mcp"
headers = { Authorization = "Bearer ..." }
```

A malformed table is grammar (exit 2, nothing spawned); a server that
cannot be reached exits 1 naming it; a tool named like one chat already
has (`docs`, `neosian`, `memory`, the skills pair, `recall_turn`, `search_history`) is refused until
the table sets `prefix`. `playground` runs the agent file as written and
reads no table; `chat --agent FILE` adds the servers. The session's
opening names each server and its tool count.

**No flags means this project.** Every verb below resolves the working
directory's layout (`user:<login>` at `/user`, `user:<login>/proj:<slug>`
at `/project`, the same pair a registered agent's sessions get) when no
`--scope` or `--mount` names a mount; `NEOSIAN_SCOPE` is `--scope`'s
environment twin. The store is the home unless `--root`, `--url` or the DSN names
one.

## Try an agent: playground and eval

```
neosian playground AGENT_FILE [--model M | --menu] [--resume ID] [--json]
neosian eval SUITE [--json] [--output DIR]
```

- **`playground`**: your agent file (it exports `configuration`, an
  `AgentConfig`) under chat's run tier, exactly as written: its tools
  and its prompt, without the resident agent's tools (`neosian chat
  --agent FILE` adds `docs`; the `neosian` tool stays the resident
  agent's). The model is `--model`, else
  the file's own; `--menu` picks it from a menu on a terminal, never
  beside `--model` and never without a terminal (exit 2). Piped stdin
  runs one turn and prints the answer, `--json` the envelope `chat`
  prints; a terminal opens a session. Turns persist under the home, and
  a file that names no memory gets this directory's layout.
- **`eval`**: runs a YAML suite over its matrix and exits 1 when a case
  fails, so it gates CI; `--json` prints the artifact's document and
  `--output DIR` names the directory it lands in (`.neosian/evals` by
  default). The paths a suite names (`agent:`, a variant's `prompt:`)
  resolve beside the suite file, so it runs from any directory; a model
  listed twice, or a YAML value JSON cannot hold (an unquoted date), is
  refused at load. The progress tree draws on a terminal only.
  Comparing models side by side is the suite's `models:` axis.

**Every prompt has a flag.** A menu or a prompt is a terminal's
convenience over a flag that exists: `--menu` picks what `--model`
names, `configure` prompts for what `--provider NAME --key -` takes, a
session's turns are what a PROMPT or piped stdin carries. `--json`
never opens a prompt: it prints one object, so `chat` and `playground`
refuse it with no turn (exit 2) and bare `configure` lists.

## Memory from the shell

`neosian memory <command>` is the shell transport over the same
dispatcher the function tool, the native Anthropic declaration, the
MCP server, and the state process's HTTP wire execute. An agent with nothing but shell access
operates the same memory the runtime transports serve.
`python -m neosian.memory` is the sandbox-safe twin for a venv whose
bin is not on PATH.

## The grammar

```
neosian memory view [PATH] [--view-range START END]   # PATH defaults to /
neosian memory create PATH --content TEXT
neosian memory str_replace PATH --old-str TEXT --new-str TEXT
neosian memory insert PATH --insert-line N --insert-text TEXT
neosian memory delete PATH
neosian memory rename OLD_PATH NEW_PATH
neosian memory maintain [--model MODEL] [--min-age-days N]
neosian memory versions PATH [--limit N]
neosian memory redact PATH [--all]
neosian memory revert PATH --version N
```

`view /` renders the memory index: the first command to try. `-` as
the value of `--content` / `--new-str` / `--insert-text` reads stdin
(the heredoc idiom; exactly one payload flag per command):

```bash
neosian memory create user/prefs.md --content - --scope user:me <<'EOF'
User prefers concise answers.
EOF
```

## Store flags (on every command)

| flag | meaning |
|---|---|
| `--root DIR` | FileStore root (created on first write); default: the home, `~/.neosian` or `$NEOSIAN_HOME` |
| `--url URL` | the state process instead of a root; its token in `NEOSIAN_CLIENT_TOKEN` |
| `--scope SCOPE` | single read-write mount of SCOPE at `/memories` (the sugar); `NEOSIAN_SCOPE` is its environment twin |
| `--mount scope=...,path=...` | explicit mount; repeatable; append `,ro` (read-only) or `,eo` (edit-only) |
| *(neither)* | this directory's project layout: `user:<login>` at `/user`, `user:<login>/proj:<slug>` at `/project`, the pair `--level project` installs render; a directory with no name refuses at exit 2 (the two doors a client spawns, `neosian mcp` and `neosian record`, serve `/user` alone there instead) |
| `--actor NAME` | who writes, `<kind>:<id>` (default `cli:local`; `cli:<host>` names the agent driving the shell) |
| `--schema NAME` | Postgres schema (Postgres only) |

Postgres arrives only through the `NEOSIAN_POSTGRES_DSN` environment
variable; there is no `--dsn` flag (argv is world-readable), and the
state process through `--url` with `NEOSIAN_CLIENT_TOKEN` set the same
way. Exactly one of the three stores is named; the others are refused. An edit-only mount (`eo`) fixes its document set:
existing documents stay editable, but nothing may be created, deleted,
or renamed there (pre-created layouts the agent works within).

## Exit tiers, everywhere

- **0**: success.
- **1**: the command ran and failed; a corrective failure rendered
  as `error: [code] message` plus a `hint:` line on stderr.
- **2**: the invocation was wrong: grammar, an unknown name, an
  invalid scope or mount. Nothing is constructed on this tier.
- **130**: interrupt.

stdout carries the artifact; stderr carries guidance, so redirecting
stdout always captures something well-formed. With `--json` anywhere in
argv, a tier-2 error also prints one `{"error": "usage", "hint": …}`
object on stdout; the tier and the stderr text stay.

## --json

On the six commands, `--json` prints the memory tool's result envelope
verbatim, one JSON object on stdout, exit 0/1 by its `success` field:

```bash
neosian memory view / --root .neosian/memory --scope user:me --json
```

`maintain` and the operator verbs print their own envelopes instead
(described below), still exactly one JSON object on stdout. Argv-tier
errors (exit 2) stay argparse text on stderr: a shell answers grammar
before any envelope exists.

## maintain: the gardener

`maintain` is not one of the six dispatch commands: it runs the
maintenance pass over the writable mounts (`neosian docs memory`).
Keyless by default (prune empty documents, merge byte-identical
duplicates keeping the oldest), and `--model MODEL` adds the semantic
pass (merge overlapping, prune stale, promote), which needs that
provider's API key: a missing key is refused at construction, never a
silent half-pass. `--min-age-days N` (default 7) protects recently
updated documents from deletion. Its `--json` envelope is its own,
`{"writes": [...], "model", "usage", "cost_micro_usd"}`, and a
requested model stage that fails exits 1 and says so on stderr while
the deterministic actions stand.

## The operator verbs: audit and remedy

`versions`, `redact` and `revert` sit beside `maintain` on the operator
side of the line: keyless acts over the store, never part of the
agent-facing six-command vocabulary.

**`versions PATH [--limit N]`** lists a document's version rows newest
first. Text output is the audit trail without content: one line per
row (version, action, actor, timestamp). `--json` carries every row's
**full content**: that is the point-in-time read, and it means history
reveals everything a document ever held. `redact` is the only eraser.
Empty history is an answer (exit 0), not an error.

**`redact PATH [--all]`** clears content everywhere for one document,
current state and every version row alike, preserving the audit skeleton
(paths, versions, actors, timestamps). A mount root redacts the whole
scope, but only with the explicit `--all`; without it the grammar
refuses. Redaction is the one irreversible act: the skeleton is
deliberately not restorable, and `revert` refuses redacted history.
Its `--json` envelope is `{"path", "scope_wide", "matched"}`.

**`revert PATH --version N`** undoes one write: `N` names the row to
undo (find it with `versions`) and must be the newest. One rule covers
every case (no live document before row N means delete, otherwise the
prior content comes back) and the revert *appends* a new version row,
never rewriting history. Its `--json` envelope is the write receipt's
fields (`command`, `path`, `version`, `previous_path`).

## Search the history: `neosian search`

`neosian search TERMS... [--conversation ID]... [--limit N] [--json]`
answers "where was this said" across every conversation the store
holds, newest first: a turn matches when it holds every term as a
case-insensitive substring (message text, tool calls and their
arguments, tool results; DESIGN §32, the one rule `neosian docs stores`
states). The words are the terms, so no quoting is needed; `--conversation`
narrows to one conversation and repeats; `--limit` is the newest N, 1 to
500 (default 20). Each hit is one line, `[<id> #<turn>] <stamp> <actor>
<snippet>`, and `--json` carries `{query, conversations, limit, client,
hits: [{conversation_id, turn, created_at, actor, snippet}]}`; on a
terminal the hits render as a table. It takes the store selection above
(`--root`, `--url`, or the DSN) and answers identically on every
substrate; there is no `--scope`, since turns carry none. Exit tiers
hold: no term, a bad id or a limit outside the range is grammar (exit
2, nothing constructed); no hit is an answer (exit 0). Inside an agent
the same search is the `search_history` tool (`neosian docs memory`).

## Continue a session: `neosian continue`

`neosian continue [CONVERSATION] [--scope SCOPE] [--json]` prints a
recorded conversation the way an agent's `continue_session` call
delivers it (DESIGN §33): the header naming the session and its writer,
the pending handoff note when it is that session's, every user prompt
and final answer verbatim with tool rounds as one line each, the older
turns as log lines under the budget. With no conversation it means what
a bare call means in the scope (default: `NEOSIAN_SCOPE`, else this
directory's project scope): the pending note's session, else the most
recent one listed under `sessions/`. A read: a terminal is not a
session, so the note stays pending and no lineage is written; the
agent's own call does both. `--json` carries `{conversation, scope,
note, client, text}`. It takes the store selection above and answers
identically on every substrate. Exit tiers hold: a bad id or scope is
grammar (exit 2); nothing to continue is exit 1 with the reason.

## The ledger: `neosian audit`

`neosian audit [--scope SCOPE] [--conversation ID] [--actor A] [--since T]
[--limit N] [--json]` answers "what was done, by whom, when" for a scope
(default: `NEOSIAN_SCOPE`, else this directory's project scope),
newest first: every memory version row (deleted documents included),
every redaction, and one conversation's turns when named. It takes the
store selection above (`--root`, `--url`, or the DSN; `--scope` is a
raw scope here, not a mount) and answers identically on every
substrate. `--actor` filters by prefix: `claude-code:s1` matches
`claude-code:s1#4` and `claude-code:s1/conv:x#2`. Through the state
process every actor carries the client prefix the daemon asserted.
Exit tiers hold; an empty ledger is an answer (exit 0).

## Moving a store: `neosian export` / `neosian import`

`neosian export DIR` writes the store to `DIR`, whole: every scope and
conversation, version history and redaction trail included, verbatim.
`DIR` is a FileStore root: `cat` it, `grep` it, `neosian serve --root
DIR` it, or `neosian import DIR` it into any other store: a fresh
root, Postgres by the DSN, or the state process by `--url`. Both verbs
take the store selection above (the home when none is named) and
`--scope S` / `--conversation C` (repeatable) to move only what they
name; naming either moves nothing of the other kind. An import needs
every unit it touches (a scope, a conversation) to be empty in the
target: an occupied one refuses the whole run before anything is
written (`memory_conflict` / `agent_conversation_conflict`, reason
`target_occupied`); nothing merges, nothing overwrites. `--json` prints
one object (`verb`, `archive`, `client`, `units` with the per-unit
counts). Exit tiers hold: 2 for a bad name or a missing archive, 1 when
a store refuses, 0 with the report (`nothing to export` is an answer).

## The record: `neosian record`

`neosian record` is what a foreign agent's hooks call: one hook payload
on stdin per event, `hook_event_name` saying which. `UserPromptSubmit`
opens a span, `PostToolUse` adds a tool round, `Stop` lands it as one
turn by `<agent>:<session_id>` in the conversation the session id names
and writes the scope's sessions document. It takes the store and mount
flags above plus `--agent KIND` (default `claude-code`), `--spool
DIR` (default `spool/` under the home, never the store) and `--project
DIR`, the directory whose layout is the default (the working directory
when absent; a once-per-machine Claude Code hook line passes
`"$CLAUDE_PROJECT_DIR"`, since a hook's working directory moves with the
agent's `cd`). The sessions document lands in the mount at `/project`
when there is one, else the first read-write mount; a neosian
`Conversation` with a writable `/project` mount writes its own the same
way, so the listing is complete. `neosian record
install --client claude-code|codex|opencode|muse-code|cursor|pi [--level user|project]
[--write]` renders or applies the hooks, the `mcp install` twin
(`neosian docs agents`). The exit tiers bend once
for the hook's sake: 2 only for argv, 1 for everything after, so a
broken store never blocks the agent. Startup context is printed on stdout;
Cursor returns it as JSON `additional_context` and returns `{}` for other
successful hooks. `--json` prints the diagnostic envelope
(`{"event", "session_id", "actor", "disposition", "conversation_id",
"turn", "document", "client", "context"}`).

## Cooperating writers per root

A FileStore root coordinates upgraded local writers through an OS lock.
Hooks, shell commands, MCP servers and embedded applications can share that
root. Restart all writers when upgrading; old versions do not take the lock.
Use Postgres for distributed writers, or the state process (`--url`) for
remote access. See `neosian docs topology` and `neosian docs messaging`.

## Durable messages and reminders

See `neosian docs messaging` for the shared `messages` tool, conversation and
scope destinations, history annotations, explicit acknowledgment and claims.
Reminders can be snoozed with a finding and a new due time on the same ID.
Native conversations opt in with `mailbox=MailboxConfig()` from
`neosian.messaging`. Regenerate client adapters after upgrading for automatic
delivery. Pi and OpenCode also support wake requests while attached.
