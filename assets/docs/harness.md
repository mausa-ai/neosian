---
title: "Cross-harness sessions: the harness tool"
summary: Start, follow, continue and stop a session in another installed harness from an agent, off by default
---

# Cross-harness sessions

The `harness` MCP tool lets an agent in one harness start a session in
another installed harness (Claude Code, Codex or OpenCode), read its output,
send it a follow-up and stop it. Each turn is one run of that harness's own
headless command, under its own login, model and permissions. The session
lives in the child harness's own store and is continued with its own resume
mechanism. neosian's watcher for the turn records each turn the child
completes through neosian's own recorder, whatever the child's hooks can do,
and its sessions document names the session that started it.

neosian holds no model key, chooses no provider, runs no loop of its own and
schedules nothing. It starts another installed harness only when an agent
calls the tool, and only when this machine's configuration enables it.

## Turn it on

The tool is off by default. Enable it in neosian's own configuration,
`config.toml` in the neosian home (`~/.neosian`, or `$NEOSIAN_HOME`):

```toml
[harness]
enabled = true
turn_seconds = 1800   # the longest one turn may run (10 to 86400)
max_children = 2      # children that are not stopped, at once (1 to 8)
directories = []      # other directories a child may work in, absolute paths
```

The setting is read when the MCP server starts, so restart the client. The
tool is listed only when all of these hold: it is enabled; the server is the
stdio server (`neosian mcp`, never the HTTP `/mcp` door of `neosian serve`);
the session is not itself a harness child; and the system is not Windows. A
`[harness]` table with a value of the wrong type, out of range or an unknown
key keeps the tool off and prints the reason on the server's stderr. When the
tool is absent it is not listed, and a call answers "Tool 'harness' not
found".

The tool is absent on Windows in this release: ending a child and everything
it started relies on Unix process groups.

## Where a child works

A harness trusts the directory it runs in, so the calling agent never picks
an arbitrary one. A child works in the MCP server's own directory, the
session's project, when that directory lies strictly below your home
directory. Any other directory must be listed by you in `directories`, as an
exact path (compared after resolving links; a subdirectory of a listed one is
not listed). When the server runs anywhere else (your home directory itself,
one of its ancestors, the filesystem root, anything outside your home), or
`HOME` is unset or empty, the tool refuses until a directory is listed, and
the agent must name one.

A child runs with that directory's own configuration for its harness, except
where the table below says otherwise, so list only directories you trust.
A `write` child in any harness can change files in the directory that tools
run later (build scripts, hooks, editor or agent configuration), so review
what it changed before you run anything there. And a child whose mode lets it
run commands can start any program: the guard that keeps children from
starting children of their own is a guard against accidents, not a sandbox.

## Commands

| command | takes | does |
|---|---|---|
| `start` | `agent` (`opencode`, `codex`, `claude-code`), `prompt`, `mode` (`read-only`, the default, or `write`), optional `model`, `directory`, `session` | starts the child's first turn and returns its `child` ID at once |
| `wait` | `child`, optional `seconds` (0 to 30, default 10) | waits until the turn ends or `seconds` pass, then returns what is new and, once the turn has ended, the answer |
| `send` | `child`, `prompt` | starts the next turn of the same harness session; refused while a turn runs |
| `stop` | `child` | ends a running turn and the child; its harness session stays in that harness |
| `list` | nothing | every child of this server, plus which harnesses are installed (`available`) and which are not (`missing`) |

`session` is your own conversation ID from the session banner (the same ID
the `messages` tool takes); with it the child is linked to you. `model` is
passed to the child harness in its own form (for example `opus`, or
`opencode/deepseek-v4.1-flash-free`); it must be 1 to 128 letters, digits and
`. _ : / @ # + -`, starting with a letter or digit. The MCP server answers one
request at a time, so while `wait` waits, other neosian tools answer after it.

Each answer is one JSON object per child: `child`, `agent`, `mode`, `model`,
`directory`, `state` (`running`, `finished`, `failed`, `timed_out`,
`stopped`), `turn`, `session` (the harness's own session ID), `conversation`
(the ID this store files it under), `record`, and `notes`. The child's words
and neosian's never share a field:

- `text` (from `wait` and `stop`) is what the child wrote since the last
  read, verbatim; `answer` is its final words once the turn has ended.
- `events` is what neosian observed since the last read, as structured
  entries: `{"kind": "tool", "name": "Write"}`, `{"kind": "denied", "name":
  "Bash"}`, `{"kind": "tool", "name": "write", "status": "error", "error":
  "Permission denied: edit"}`, and `{"kind": "truncated", ...}` when
  something was left out. A Codex command shows as `{"kind":
  "command_started", "id": "item_1", "command": "sleep 120"}` while it runs
  and as `{"kind": "command", "id": "item_1", "command": "sleep 120",
  "exit": 0}` once it completes: entries with the same `id` are one command.
- `notes` are neosian's own sentences: what to do next, where the turns are
  recorded, whether the child is linked.
- A failed turn adds `exit` and `error`.

## Signing in

A child uses its harness's own sign-in, and it cannot sign in for itself: it
runs headless, and on macOS its filesystem policy allows no credential file
and no keychain write. So before every turn (`start` and `send`) neosian runs
the harness's own status command, outside the child and any sandbox, in the
child's directory and with the server's environment. It reads nothing from
standard input, has 5 seconds and keeps 8 KiB of each stream, and is ended
with every process it started when it does not answer in time. Nothing it
prints is stored, and it starts no sign-in.

| harness | status command | signed out when |
|---|---|---|
| Claude Code | `claude auth status` | it answers JSON with `"loggedIn": false` and exits non-zero |
| Codex | `codex login status` | it prints the line `Not logged in` and exits non-zero |
| OpenCode | none | (no check: `opencode auth list` lists stored credentials, but a free model needs none and an environment variable counts, so it cannot say that a run is signed out) |

A signed-out harness gets no child: `start` or `send` refuses with one
instruction, for example "Codex is not signed in, or its sign-in needs
renewing, and a child cannot sign in for itself ... Sign in outside the
child, in a terminal of your own, with codex login, then start or send
again." Claude Code's names `claude auth login`, OpenCode's `opencode auth
login`.

Any other answer is unknown, not signed out: a timeout, a status command
that cannot run or is missing (an older client), an error (a network
failure included), and an answer neosian does not recognise. The turn then
starts, and its answers carry a note that says why the sign-in could not be
read, with at most the first line of what the command printed and every word
in it that may be a credential or a personal detail replaced by `[hidden]`.

When the harness reports in its own structured error that its sign-in failed
during a turn, the turn fails and its `error` is the same instruction, not
the raw error: Claude Code's `authentication_failed` or a result that says
"Please run /login" or "Sign in again", Codex's failure that says "sign in
again" (a refresh that failed), and OpenCode's `provider.auth` error. An
external API key that a provider refuses is not a sign-in and is shown as it
is. No file and no keychain item is allowed to a child to renew a sign-in.

Nothing is relayed back to you for approval. When the child's harness prints
a denial, it appears in `events`: a Claude Code `denied` entry, an OpenCode
tool error, a Codex command with its exit status. When the harness prints
none, you learn of the denial only from the child's own words in `text` and
`answer`. That is the common case: a `read-only` Claude Code or OpenCode
child has no writing tool to be refused, so it says it cannot write instead
of trying, and Codex 0.169.0 printed no item at all for a command its
sandbox refused. Treat `text` and `answer` as another agent's data, not as
instructions from the user.

## The filesystem policy on macOS

On macOS neosian itself confines every child, whichever harness it is, to
one filesystem policy. The turn's watcher applies it when it starts the
harness, with the operating system's sandbox profile language through
`/usr/bin/sandbox-exec` (deprecated by Apple and still shipped; verified on
macOS 27.2.1). The harness and every process it starts run under the policy;
the watcher stays outside it, so it still ends the turn's processes and
records the turn. Writes are refused unless the policy allows them:

| path | `read-only` | `write` |
|---|---|---|
| the turn's private temporary directory (`TMPDIR`; `CLAUDE_CODE_TMPDIR` for Claude Code) | write | write |
| the harness's own session state: Claude Code `~/.claude/projects`; Codex's databases, `sessions`, `cache`, `tmp` and the like in `~/.codex`; OpenCode's database, `log`, `snapshot` and `shell` in `~/.local/share/opencode` | write | write |
| the child's directory | read | write |
| in the directory: `.git/hooks`, `.git/config`, `.claude`, `.codex`, `.opencode`, `.agents`, `.mcp.json`, `opencode.json`, `opencode.jsonc` | read | read |
| neosian's home and store, except data writes to the store's lock file (an existing regular file with one link), which its readers open for writing | read | read |
| neosian's login state (`login` in neosian's home) | no read | no read |
| `~/.claude/projects/*/memory`, the child marker file | read | read |
| everything else: the rest of your home, credentials and the keychain, other projects, configuration such as `~/.claude.json` or `~/.codex/config.toml` | read | read |

Only the harness's own state is writable, never another harness's, and a
state name that leads anywhere but its own place (a link to the state
directory itself, for example) refuses the child, so configuration and
sign-in files there never become writable through an alias. Every
path is resolved to its real location before the policy is written, so a
symbolic link leads only where a write was allowed; a state directory that
`CLAUDE_CONFIG_DIR`, `CODEX_HOME` or `XDG_DATA_HOME` moves must stay below
your home directory. The policy also closes preference writes through
`cfprefsd`. A refused write reaches the child as "Operation not permitted",
and appears in `events` when the harness reports the failed tool.

If the policy cannot be applied, the child is not started: `start` and
`send` refuse before any client command runs, and the watcher refuses
again. That happens when `sandbox-exec` is missing, and when this neosian
server itself runs inside another sandbox (macOS lets a sandboxed process
apply no other profile). The server and the watcher each prove the turn's
policy on one write it must refuse, at a place no allowance covers, before
anything starts. Codex's `mcp get` and `mcp list` checks run under that
policy and with the child's environment; the client's sign-in status runs
outside it (a status command may need to save a refreshed sign-in, which
the policy refuses), but without the store credentials below.

No child holds the store's credential, in either mode: its environment
loses `NEOSIAN_CLIENT_TOKEN`, `NEOSIAN_POSTGRES_DSN`, `NEOSIAN_LOGIN_TOKEN`
and the variables the server's profile names for its sources, and
neosian's login state is unreadable to it. A child of a store reached with
a credential (`--url`, Postgres, a profile with an HTTP or Postgres source,
or a signed-in neosian cloud profile) therefore has no neosian memory of its
own: its own neosian tools fail at once, and its answers say so. It still
starts in either mode, and its watcher, outside the policy, records its
turn in this server's store, where you read it. No child writes neosian's
store: on a local file store a child's own neosian tools can read memory
but not change it.

The watcher uses the server's own store selection, which the server passes
on explicitly (a profile it resolved from its own directory included); it
never selects again from the child's directory. A child in a directory
bound to another profile is recorded under the server's profile, with the
server's capture policy, and the server's store is the one the policy
protects. When the watcher cannot establish that store, the child is not
started.

What the policy does not protect:

- **The network is open.** A child can reach any host, local services
  included, and a child with a shell can change things elsewhere (push,
  call an API) in either mode. A filesystem policy gives no confidentiality
  against a network the child can reach.
- **Reads.** A child can read what you can: your secrets, credentials and
  keychain items the `security` tool reads, and other projects.
- **Outside services.** A service already running outside the policy acts
  with its own rights when the child talks to it (an OpenCode server,
  Docker, ssh-agent).
- **State that steers later sessions.** The harness's own session state is
  writable, and text written there can reach a later session.
- **A sign-in that needs refreshing.** The policy allows no credential file
  and no keychain write, so a child whose harness must refresh its sign-in
  fails, with the instruction to sign in outside the child (see "Signing
  in").
- **Code that runs later** in a `write` child's directory (`Makefile`,
  package scripts, CI files), and a hard link that already points outside.

Elsewhere, Linux included, neosian applies no common policy in this version:
each harness keeps its own preset below, and every child's answer carries a
note that says so. Windows has no `harness` tool.

## Modes, per harness

Each mode is also a preset that the child harness enforces with its own
mechanism. `read-only` is the default and the strongest each harness can
give. They are not equally strong. On macOS the presets apply inside the
common policy, which holds whatever a preset allows, with one change: Codex
cannot start its own sandbox inside another, so a Codex child there runs
with `--dangerously-bypass-approvals-and-sandbox` in place of `-s` and
`approval_policy`, and the common policy is its boundary. Claude Code's and
OpenCode's presets are unchanged.

| harness | `read-only` | `write` |
|---|---|---|
| Claude Code | `--setting-sources user`: no project or local settings, so no hooks or allow rules from the directory; `--strict-mcp-config` with one server, this neosian, started with every mount read-only, whose tools it may use; built-in tools Read, Grep and Glob only (`dontAsk`) | the person's normal configuration with `--permission-mode acceptEdits --permission-prompts none`: edits inside the directory are accepted, and so are Claude's own filesystem commands there; anything that would ask is denied, including paths outside the directory |
| Codex | `-s read-only -c approval_policy="never"`: the operating system sandbox refuses every write by a command the model runs; every other configured MCP server is switched off for the run (one listed without a usable name, or with a name an override cannot address, makes `read-only` refuse to start), and so are Codex's apps (`--disable apps`); neosian's own stdio entry (`neosian-memory` or `neosian`) runs this neosian executable (never a wrapper the entry names) with every mount read-only; an entry over HTTP and the hosted `neosian-cloud` entry are switched off | `-s workspace-write -c approval_policy="never"`, with `/tmp` and `TMPDIR` excluded: commands may write inside the directory only |
| OpenCode | `OPENCODE_DISABLE_PROJECT_CONFIG=1`: the `opencode.json` (agents, MCP commands) and `.opencode` plugins of the directory and its ancestors are not loaded; an agent that denies everything except read, glob, grep and list (`.env` files denied) | `OPENCODE_DISABLE_PROJECT_CONFIG=1` too, so nothing the child writes is loaded in its own session; the same agent plus edits, except to `opencode.json`, `opencode.jsonc` and anything under `.opencode`; `external_directory` denied |

Under a portable profile the profile fixes the mounts, so a read-only child's
neosian cannot be started with them read-only; Claude Code then allows only
neosian's tools that cannot change state (`list_skills`, `load_skill`,
`recall_turn`, `search_history`, `docs`, `memory_history`, `search_memory`,
`upkeep_review`, `knowledge`, `changes`), and Codex enables only those.

What each does not guarantee:

| harness and mode | not guaranteed |
|---|---|
| Claude Code `read-only` | Your own user settings still apply: your hooks run, and plugins you installed load. |
| Claude Code `write` | The directory's own `.claude` settings, hooks and `.mcp.json` servers load and run, and its allow rules apply. Commands that your allow rules cover run anywhere they reach. MCP tools of your other servers that your settings allow run. |
| Codex, either mode | The sandbox covers the commands the model runs, not MCP tool calls. Codex refuses an MCP tool call that needs approval when approval is `never` (observed, neosian's tools included), so a read-only child in practice uses no MCP tool; a tool your Codex configuration approves in advance runs. A project you marked trusted in Codex loads its own configuration and hooks; an untrusted one's are ignored. `approval_policy = "never"` is always passed: without it, `codex exec -s read-only` wrote a file in our test. Network follows your configuration. Codex takes a field of an MCP entry only for that entry's transport (codex-cli 0.169.0: "env is not supported for streamable_http"), so neosian reads each of `neosian-memory`, `neosian` and `neosian-cloud` with `codex mcp get` and passes only what its transport takes: a stdio entry gets the child marker in `env` and `harness` in `disabled_tools` (and, read-only, this executable and read-only arguments when it is `neosian-memory` or `neosian`); an HTTP (`streamable_http`) entry gets `harness` in `disabled_tools` in `write` and `enabled=false` in `read-only`. An entry with another transport refuses the child by name, in either mode; another server with one refuses `read-only`. |
| Codex `read-only` | Codex's own built-in tools stay: web search, image generation and spawning Codex's own sub-agents were among a read-only child's tools in our test. neosian does not switch them off and has not checked what each can reach. |
| Codex `write` | Your configured MCP servers and apps stay on. The child is not given `--skip-git-repo-check`, so Codex refuses a directory that is not a Git repository (read-only skips that check). |
| OpenCode, either mode | OpenCode has no sandbox for shell commands, so neither mode allows the shell, and no MCP tool runs: an OpenCode child has no neosian tools at all. Your own OpenCode configuration and plugins load, and OpenCode still starts the MCP servers your configuration registers (observed: your own neosian server, on your store with its usual access), although the preset denies all their tools. OpenCode also lists its own `skill` tool to the child: OpenCode appends its browser rule for `skill` after every agent's rules, so no agent can hide that tool, but the preset refuses every call of it (read from OpenCode 2.3.2's agent listing and code, not observed in a run). |
| OpenCode `write` | OpenCode's permission rules match letter case exactly and macOS file systems do not, so a child refused `.opencode/plugin/x.js` can write `.OPENCODE/plugin/x.js`; it is not loaded in the child's own session, but a later OpenCode session in that directory may load it. A child can also write through a symbolic link inside the directory that points outside it: OpenCode checks the path, not where it leads. |

Your client's approval of the `harness` tool is the gate. A child can do what
its mode allows whatever the calling agent's own permissions are, so a
read-only parent that starts a `write` child can change files through it.

## Reading the child and the parent link

Each turn runs under a watcher, a neosian process outside the child. The
watcher reads the harness's own headless output as it relays it and, when
the harness completes the turn, records it through `neosian record`, as the
child's hooks would have: the prompt, each completed tool call with its input
and result, then the answer. So the turn follows the recorder's rules
exactly: capture consent and capture off, `neosian record pause`, the keep
limit, and the store and policy the turn was closed under, through the same
queue and lander as any hook's turn. The recorder writes to the store this
MCP server reads, with the child's directory as its project.

What it records is what the child's own hooks would have recorded, read from
the output the harness printed, never from the shortened output the tool
returns to you: Claude Code's prompt, each tool call that succeeded with its
structured result, and its final answer; Codex's prompt, each completed
command, file change, MCP call and web search, and its last message;
OpenCode's prompt, each completed or refused tool call, and the text of its
last step. Only a turn the harness reports completed is recorded: a turn
that failed, was stopped, timed out or passed its limit is not recorded, and
a note says so. A turn is recorded whole or not at all: one line of the
harness's output past 8 MiB, or one turn's tool calls and answer past
64 MiB, records nothing for that turn, and a note says so. Everything the
watcher holds of the turn counts toward that bound (its tool calls, the
words it keeps for the answer, and the inputs of tool calls whose results
have not arrived), and once the bound is passed it lets go of all of it and
reads no more of the turn, while the output still reaches you. While the turn
runs, the watcher keeps its tool calls in a temporary file that has no name
in any directory, which goes with the watcher.

When the child's turn is recorded in the store this MCP server reads,
`record` names the conversation and the turns that landed:

```json
{"record": {"conversation": "5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe", "turns": "2"}}
```

Read a turn with `recall_turn(n, conversation="...")`. The conversation is
the harness's session ID (under a portable profile, the ID the profile
derives from it). Only the store says a turn is recorded. When it is not
there yet, a note says why: it waits in the record queue for its destination
(a later call confirms it), capture is off for the profile, recording is
paused for the conversation, the profile's policy keeps none of it, the
harness did not complete it, or it passed the watcher's bound. The answer is
in the tool's own output either way.

A turn is recorded once. The child's own neosian hooks spool nothing for the
session its watcher records: in a child, the record verb answers
`watched` for that session (the session `NEOSIAN_HARNESS_SESSION` names, or
any session in a Codex or OpenCode child's first turn, whose session is not
known before it starts). A hook that knows nothing of this, such as one of a
release before 3.0.0 or one whose harness clears its environment, may still
land a new session's first turn itself; the watcher then finds the turn in
the store and does not record it again. It does not find a continued
session's turn that way, so hooks from an older neosian beside this one can
record a continued turn twice. The watcher records a turn only under the
session it started or continued: when a harness announces a session that
already existed in this store, the tool says so, records nothing into it and
neither describes it as this child's record nor links it. Such a child is
never continued: `send` refuses, whether the server or the watcher saw it
first, so no follow-up turn reaches that conversation.

With `session`, the watcher records the turn with `- parent: <your ID>` in
its sessions document. Where that document lacks the line, the server writes
it itself into a sessions document in a scope it mounts read-write, but only
one that did not exist when the child's session was announced and whose
agent is the child's harness. A note says "Linked" only once the line has
been read back; a line the server just wrote is confirmed at the next call.
A child started in another project's directory is recorded under that
project and is not linked by the server, with a note saying why.

The watcher's record and the parent link were verified with stand-in
harnesses that print what the real ones printed, by reading the store. They
have not yet been checked with the real harnesses.

## Children never get the tool

A child cannot start children of its own by accident. Every layer the
harnesses support is used:

- Every child carries `NEOSIAN_HARNESS_CHILD` in its environment; a neosian
  server that sees the variable, whatever its value, omits the tool, and
  `start` refuses.
- Claude Code: `--disallowedTools mcp__neosian-memory__harness
  mcp__neosian__harness` removes the tool under either server name.
- Codex passes its MCP servers only a fixed set of variables, so the child's
  `TMPDIR` is a private directory holding a marker file the server checks.
  For `neosian-memory`, `neosian` and `neosian-cloud`, whichever `codex mcp
  get` finds, the child also gets that entry's `disabled_tools` with
  `harness` added and, when the entry is stdio, its `env` member; the
  `harness` tool is never served over HTTP. On macOS every child's `TMPDIR` is that private
  directory, and the common policy refuses removing the marker.
- OpenCode: both preset agents deny `neosian-memory_harness` and
  `neosian_harness`.

## Bounds and ending

`start` and `send` return after starting the turn, waiting at most 3 seconds
for a new session's ID. Before a Claude Code or Codex turn, the sign-in
check takes 5 seconds at most. Before a Codex turn, neosian checks Codex's
configuration with three `codex mcp get` calls, and in `read-only` a fourth,
`codex mcp list`, 5 seconds each at most, and the turn's watcher repeats them
before it starts Codex. `wait` waits at most 30 seconds. `stop` returns
within about 15 seconds. A turn past `turn_seconds` is stopped and reported
as `timed_out`. A stop that reaches the watcher after the harness completed
its turn but before the watcher began to record it prevents the record, and
the answer says so; once recording has begun, the stop does not cut it, and
the answer says the turn is recorded.

Ending a turn is best effort. Whether it is stopped, passes its limit, or the
MCP server exits for any reason, including being killed, neosian walks the
harness's process tree while the harness is still running, sends SIGTERM to
each descendant and its process group (Codex starts each command in a group
of its own), then to the harness, and after 5 seconds sends SIGKILL to what
is still the same process (same process ID and start time) or appeared
since. When a turn ends normally its answer is there at once; processes left
in the harness's process group get 20 seconds and are then ended, sooner if
the child is stopped or the server exits. The watcher records the turn
before the answer arrives; a turn too large to land at once is left to the
recorder's detached lander, which no ending reaches.
If the turn's watcher process is killed, the server ends its process group
itself. A process that detached from the harness's process group before a
walk, or after the harness exited normally, can escape.

Each answer returns at most 16 KiB of new text (the newest; the bytes left
out are stated in `events`), at most 32 events (the entries that state a cut
included), the first 16 KiB of the
answer (with its full size) and 4 KiB of an error; `list` shows no text,
512 bytes of an error, and stays under 64 KiB as sent, leaving out the
oldest children past that. A turn keeps the first 64 KiB and the last 192 KiB of
its text and its last 256 events. The prompt reaches the child on standard
input, never on a command line or in a file, and is at most 256 KiB.
Commands run from an argument vector, never through a shell, and the harness
is found only on absolute `PATH` entries. A read-only child's neosian server
is started with this server's store flags on its command line, so a server
whose store URL carries user information (`https://user:secret@host`) starts
no child: the watcher refuses the turn.

## Environment and versions

The child inherits the environment the MCP server was started with, plus
`NEOSIAN_HARNESS_CHILD`, `PWD` set to its directory (OpenCode takes its
project from it), and the variables above; on macOS also the policy's
`TMPDIR`, without a `read-only` child's store credentials. Codex starts MCP servers with a
short list of variables, so children of a Codex session see only those (no
`CODEX_HOME` and no API keys from your shell).

Observed on 2026-11-21 with Claude Code 2.1.330, codex-cli 0.169.0 and
OpenCode 2.3.2. OpenCode's default model may bill credits you do not have;
pass `model` (a free model OpenCode lists works). Other versions may print
other events or refuse an option; the turn then fails and the error is
reported.
