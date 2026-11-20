---
title: "Any agent: the record through hooks"
summary: Agent setup, native Codex upkeep, recording hooks, startup context and Claude Code cache maintenance
---

# Any agent

Portable profiles add explicit source selection and recording policy to all
six supported clients. Use `setup --profile NAME` (or the standalone
installers' `--profile NAME`) and inspect the capture fields, destination and
`profile_support` report. New profiles capture off by default; startup context
and memory remain available. Local/shared recording requires both profile and
binding consent, filtering precedes the spool, and new conversation IDs retain
native identity as provenance under a workspace namespace. Read `neosian docs
profiles` for this policy. The session-ID and automatic-recording behavior
below describes existing profile-free hooks.

neosian is the state layer for the agent you already use. The memory
half is the MCP server and `neosian mcp install` (`neosian docs mcp`).
The record half is `neosian record`: the agent's hooks call it with one
payload on stdin per event, and a prompt-to-stop span lands in the store
as **one turn with a foreign actor** (`claude-code:<session_id>`) in a
conversation keyed by the session id, plus a small **sessions document**
under the scope. Transcript files are never read: the documented hook
payloads are the import path.

A logged-in machine can register clients with `neosian setup --write` to follow
the checkout's assigned workspace and fall back to the machine default. An
explicit `setup --profile NAME` pins the registration. Restart each client
after setup and inspect the selected workspace with `neosian status`.
See `neosian docs login` for machine credentials and recording controls.

## Install

`setup` and `status` are provided by the binary.
Setup registers the executable running it, or the binary named by
`--binary PATH`. The binary also provides the standalone `record
install` and `mcp install` forms below. MCP installation also supports
Claude Desktop, which has no recording hooks.

```bash
neosian setup --write                                             # select clients, once per machine
neosian setup --check --client codex                              # inspect saved settings and test MCP locally
neosian setup --url http://127.0.0.1:6367 --write                 # the same, behind the state process
neosian record install --client claude-code                       # print the user-level hooks
neosian record install --client codex --write
neosian record install --client opencode --level project --scope user:me --write
```

- **`neosian setup`** runs this installer and `mcp install` for every
  client it finds (their config directories are the evidence), prints
  first. On a terminal `--write` asks which clients to connect, all selected
  initially. Scripts use `--write --yes` for all detected clients or repeated
  `--client C` for an explicit selection. JSON never prompts. Both setup and
  status accept invocation-only `--at CLIENT=DIR` overrides and report where
  they searched; a missing client home is never created.
  Claude Code setup also installs the cache plugin and observation bridge
  described below; the standalone MCP/hook installers do not.
  Codex setup installs the native upkeep skill described below.
  Setup and status show running, selected and PATH executable paths.
  `status` also reports the MCP and hook executables separately, their storage
  selection and capture policy. PATH differences remain visible even when only
  one command is on PATH and another binary is running by absolute path.
- **`neosian setup --check`** inspects existing registrations and runs a bounded
  MCP initialize/list/create/read probe through the running binary, using a
  temporary file store that is removed afterward. It does not execute commands
  found in client configuration, open configured stores, contact a model, or
  rewrite registrations. Missing executables, differing MCP/hook storage,
  incomplete profile bindings or credentials and duplicate hook files produce
  exit 1; passing checks produce exit 0. `--json` separates `installation`,
  `storage`, `clients`, `probe` and `issues`. An explicit `--root`, `--url` or
  `--profile` also checks registrations against that requested selection.
  Checks use this shell's environment; each client still needs its own credential
  variables. Source access is `not_checked`, and client connection/hook delivery
  are `not_observed`. Restart the client, review hook trust and confirm its MCP
  connection. `--check` and `--write` are separate steps.
- **`neosian setup --refresh`** is what `neosian update` runs on the release
  it installed. It brings existing user-level registrations to that release
  with the same clients and store selection, keeps each registered command
  line word for word, and connects nothing new. It changes only what the
  release owns and needs to change: a hook group that already carries the
  neosian command is kept with whatever you added to it, values you set in an
  MCP entry or the cache statusline stay, and symlinked files keep their link
  and mode. Persisted opt-outs stay. A part it cannot change faithfully is left
  alone with the reason and the fix, and the run still succeeds: another
  installation's registration, words this release does not write, a read-only
  file, a locally edited skill, a statusline without the bridge, or a missing
  `claude` CLI. Project files are listed, never edited. Only a failed write
  fails a client: its files are put back unless the client changed them
  meanwhile, and the exit is 1. Run it again to retry or to complete an
  interrupted refresh. Restart the clients it names. JSON never prompts. Run it
  yourself after a package-manager upgrade.
- **Once per machine.** By default the hooks land in the client's own
  settings (`--level user`) and serve every project: with no store flag
  the line names `~/.neosian` (or `$NEOSIAN_HOME`) and no mount, and the
  verb derives each session's layout where it runs: `user:<login>` at
  `/user`, `user:<login>/proj:<slug>` at `/project`, the slug the
  project directory's name. Nothing is written into a project. CLI operations
  use the same local defaults (`neosian docs quickstart`).
- **`--level project`** writes this directory's file instead, with its
  layout spelled into the line: a scope of your own for one project
  (`--scope`, `--mount`), or recording only where you ask for it. A
  lighter per-project override needs no second registration: set
  `NEOSIAN_SCOPE` in that project's environment for the client. Muse clears
  this variable at launch, as does Cursor for MCP children; name their
  scopes explicitly when installing.
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
  Cursor uses the payload's workspace roots; see its section below.
  A directory with no name to derive a project from (`/`) records to
  `/user` alone; a hook never exits 2, which Claude Code reads as
  "block the prompt".
- Print mode (the default) puts the paste-able `hooks` fragment on
  stdout (Muse shows its multi-file change preview instead; the one command on `UserPromptSubmit`, `PostToolUse`, `Stop`
  and `SessionStart`) and guidance on stderr.
- `--write` merges it into `~/.claude/settings.json` (under
  `$CLAUDE_CONFIG_DIR` when set), preserving every other key, event and
  hook; our hook is replaced inside its group, other hooks in that group
  stay, and a re-run is idempotent. `~/.claude` must exist: neosian never creates another
  program's config home.
- The store flags are `mcp install`'s; the hook line adds `--spool`
  (absolute) and `--agent` when not the default. A DSN is never written
  into a hook line; `--url` carries the token through
  `NEOSIAN_CLIENT_TOKEN` in the client's own environment, which is now
  a setting for the machine, not for one project.
- Hooks and MCP can share a local FileStore root through its cooperating process
  lock. For remote storage, `neosian setup --url URL --write --yes` selects an
  explicitly operated state process, or use Postgres. Read `neosian docs topology`.
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
  project`): OpenCode 2's one plugin definition, whose `setup` maps the
  `prompt` and `execute.after` hooks and the session's execution events
  onto the verb's three write payloads and pipes them in, carrying the
  inbox frame as a system part of the turn's requests and the
  assistant's reply from the text events; ours whole, overwritten on
  re-run, never merged; print mode prints its source. OpenCode 1.x loads
  only the file an earlier release wrote. The writer is
  `opencode:<session_id>`. OpenCode has no
  start hook, but it shows an MCP server's instructions to the model
  whole, so its read side is the server's instructions: the memory
  index, the pending handoff note and "where we left off", rendered per
  connection (DESIGN §33). The memory half is JSON: `neosian mcp
  install --client opencode` merges `{"mcp": {"neosian-memory": {"type":
  "local", …}}}` into OpenCode's config in the same directory: into
  `opencode.jsonc` when that file stands there holding plain JSON (the
  file OpenCode's own init leaves), else into `opencode.json`. OpenCode
  reads both files and merges them, the `.jsonc` last, so beside a
  commented `.jsonc` the entry lands in `opencode.json` and the report
  says so (and warns when the `.jsonc` names `neosian-memory` itself,
  since that entry would win). Any model OpenCode can run works, its
  free models included.

## Codex upkeep in the prompt picker

`neosian setup --client codex --write` installs `$neosian-upkeep` alongside
MCP and hooks. `neosian update` keeps an installed entry current unless you
edited it, which the report says; an installation that predates the skill
needs this setup once. Then run
`neosian setup --client codex --check`. In the Codex prompt area, type:

```text
$neosian-upkeep Review /project and show proposed changes before applying anything.
```

The entry is also available through Codex's `/skills` picker. Restart Codex
if the picker has not refreshed. The existing conversation loads the live
`builtin:upkeep` workflow through MCP and performs the review. Profiles must
select `builtin:upkeep`; new profiles do. Approval, paused writers and mounted
permissions still govern apply. Nothing is scheduled by installing the skill.

Setup writes `skills/neosian-upkeep/SKILL.md` and its display metadata under
the selected Codex directory (`~/.codex`, `$CODEX_HOME`, or `--at codex=DIR`).
The `neosian-skills.json` receipt identifies managed bytes. Preview writes
nothing; repeated setup refreshes owned files, repairs missing files and
resumes interrupted updates. Foreign files survive. Local edits, unmanaged
name collisions and symlinks are refused before changing registrations.
`setup --check` fails on missing, outdated or conflicting managed entries.
It checks files; actual Codex discovery and client enablement remain unobserved.

`neosian setup --client codex --codex-skills off --write` removes the managed
entry and persists the opt-out. Normal setup preserves that choice; use
`--codex-skills on --write` to re-enable it. User-edited files are preserved
and must be moved or reconciled before removal or update. Other native skills
and stored neosian skills are left alone.

## Claude Code cache maintenance

`neosian setup --client claude-code --write` installs the bundled
`neosian-cache@neosian-local` plugin through Claude's public plugin CLI and
composes a local observation bridge with your existing statusline. Restart
Claude Code after setup. Ordinary `claude` launches work; no patched binary,
extra mode, separate model account or background Neosian daemon is needed.
Claude Code 2.1.314 is the tested baseline. The plugin accepts subsequent
2.1 patches only while the required public events and receipt fields match.

Keep-warm defaults on until the session closes. After `/clear` or a resume,
keep-warm resumes for the new conversation with session controls back at their
defaults, waiting for its first message. After a successful ordinary
request, it matches Claude's documented cache observation to that request's
usage. An observed one-hour subscription cache becomes eligible for a hidden
refresh near minute 55 from that request's start, while idle. Normal conversation
activity postpones refresh; verified refreshes also renew the deadline.
The request uses Claude's public `model.fork` API and the session's current
model and account; it creates no main conversation turn and cannot run tools.
The Rust memory, search, capture and MCP paths do not run the timer or model.

If the laptop wakes after expiry, the cache lifetime is unknown, context or
model changes, a refresh fails verification, or a usage limit is reached or
nearly exhausted (see "Waiting for a reset" below), automatic calls stop and the
badge warns. The feature stays enabled: the next successful ordinary message
can establish a new warm cache and rearm maintenance. It does not deliberately
rebuild an expired cache just to produce a small reply. Five-minute caches
are observed without automatic refresh; setup does not change Claude's TTL.

| In Claude Code | Effect |
|---|---|
| `/neosian-cache` or `/neosian-cache status` | Show status without making a model request |
| `/neosian-cache off` | Stop maintenance for this session |
| `/neosian-cache on` | Enable until close; still requires an observed warm one-hour cache |
| `/neosian-cache 2h` | Enable for a duration (minutes or hours, at most seven days) |
| `/config` | Persist the plugin's `enabled`, `allowApi`, `usageNotes` and `waitHorizonHours` preferences |

Subscription usage indicators and a one-hour TTL are required by default.
An explicit `on`, duration or persistent `allowApi` also opts usage-billed
sessions into maintenance when a warm one-hour cache is observed. Requests
consume the account's normal usage. There is no promise of quota savings.

The compact neosian line above the prompt shows expected warmth and keep-warm
state, wrapping in narrow terminals. Healthy caches have no warning icon;
amber indicates a paused or uncertain state. `/neosian-cache` opens a details
panel with pause reasons and the last refresh's actual cache reads, writes and
output tokens. Press Esc to close it; the panel adds nothing to the conversation.
A successful refresh
must read nearly all the observed prefix and return the expected short reply;
a surprising receipt prevents further automatic attempts. Reported usage can
include reasoning tokens even for a one-word visible reply. Checks
allow at most 256 output tokens, 1,024 uncached input tokens and 2,048 cache-write
tokens, require at least 90% prefix reuse, and allow at most 2,048 unreused prefix
tokens. These limits are checked after completion. Cache status is a TTL
estimate, not a live server probe. Claude's public fork API
has neither an only-if-cached condition nor a hard output-token cap: an
unexpected miss can only be detected after that one request has occurred.

Setup preserves the previous statusline command and feeds it the original
JSON. The bridge uses Bash, or Git Bash/PowerShell on Windows, bounds delegated
output to 64 KiB and waits at most five seconds. On Windows it honors
`CLAUDE_CODE_GIT_BASH_PATH` and finds standard Git Bash locations before falling
back to PowerShell. Its refresh interval is at most 30 seconds, preserving any
faster existing interval. A missing plugin heartbeat produces a fallback
warning. `neosian status --json` and `neosian setup --check` inspect installed
assets and observation wiring; they explicitly do not claim live delivery.
`neosian update` refreshes the bundled plugin through `setup --refresh` while
Claude lists it enabled and `claude` is on PATH; the statusline keeps the values
you set on it. After a package-manager upgrade, run `neosian setup --refresh`
yourself. Normal
`claude update` does not remove the plugin; incompatible behavior pauses it.

To remove the integration from this setup scope, run
`neosian setup --client claude-code --claude-cache off --write`. This disables
the plugin and restores the saved statusline if Neosian still owns its wrapper.
Project removal restores inheritance when no project statusline existed.
Disabling stops future refreshes; an already-sent request may finish.
An explicit plugin disable survives later setup runs. To restore installation,
enable `neosian-cache@neosian-local` through `/plugin`, then repeat setup.

The bridge stores bounded observations and refresh receipts under
`neosian-cache/observations/` beside the selected Claude settings file. These
contain session IDs, host/model IDs, counts, usage percentages, status and UTC
times; no prompts, response text, transcripts, credentials or memory documents.
One observation and one receipt are overwritten per session. They are local
diagnostics, not memory or an authority to resume refresh after a restart; old
session files may be removed while Claude is closed.

### Usage notes before a limit

Claude Code shows you how much of a subscription usage limit is used but never
tells the model. Reaching the limit cuts the turn in progress. The plugin tells
the agent beforehand so it can stop at a clean point.

The figures are Claude Code's own: the percentage used of the account's 5-hour
and weekly windows and each window's reset time, as the session's last response
reported them. The plugin reads them without a model request when you send a
message, when a tool call finishes and every 30 seconds while the keep-warm
timer runs, and records a reading only after the session has received a new
answered response, its own or a subagent's. An idle session and a failed
request record nothing. The pace
is the steepest rise of that percentage against any reading taken 90 seconds
to 15 minutes earlier, in percent per minute: observed movement, not a price
or token estimate. Minutes left is the remaining percentage divided by that
pace. A keep-warm reserve (see "Waiting for a reset") can bring a wrap-up
forward, but the note always states the minutes to the limit itself.

| Note | When | Asks the agent to |
|---|---|---|
| Notice | 85% of a window used | Prefer finishing work in progress over starting large new work or several agents at once; nothing needs to stop yet |
| Wrap-up | 95% used (less a reserve of at most 5 points when keep-warm can refresh and the reset is within the horizon), or 10 minutes or less left at the recent pace once 50% is used, unless the window resets first | Finish the edit in progress, start no new tasks or agents, and say briefly what is done and what remains |

Each note is delivered at most once per window in a conversation; after
`/clear` or a resume the new conversation receives its own. A wrap-up note
replaces a notice not yet delivered, except that a wrap-up raised by pace
below 85% leaves the notice and the wrap-up at 95% (less any reserve) still to come. With both
windows at wrap-up, one note covers both and describes the one that resets
later, since waiting for the earlier reset would not help. A notice is held
back while the other window is at wrap-up; a window that has received its
wrap-up note stays at wrap-up until it resets, even if a smaller reserve would
now put it below that level. A new window (its reset time moves)
starts over; a fall of a point or two inside a window is treated as noise, and
a window whose reset time has passed is not shown and raises nothing until a
fresh reading arrives. In the main conversation the note arrives as context
with the next tool result or your next message, unless another hook blocked
that event. Subagents receive only wrap-up notes, once each per window, asking
them to finish the step in progress and return, stating what is done and what
remains.

The wrap-up note also says what comes next. When the window resets within the
wait horizon (6 hours by default), it suggests that, if you want the remaining
work completed, the agent schedule a continuation for just after the reset with
Claude Code's own scheduler and then end the turn. When the reset is further
away or unknown, it asks for a handoff note so a later session can resume.

Notes are guidance only. neosian never submits a prompt, blocks or stops a
turn, schedules or runs an agent, or changes a Claude Code setting. Claude
Code's own wait-and-continue at a reached limit is unchanged. Notes also reach
non-interactive sessions and need no observation bridge. In `/config`,
`usageNotes` (on by default) turns them off and `waitHorizonHours` (6 by
default, 1 to 10) sets the horizon. `/neosian-cache off` stops keep-warm
only; notes follow `usageNotes`.

Once a window reaches the notice level, the neosian line above the prompt adds
`usage 87%`; the line turns amber at the wrap-up level and stays amber after a
wrap-up note until that window resets. `/neosian-cache` lists
each known window, the last note delivered with its UTC time, and whether usage
notes are on, off or unavailable:

```text
5-hour usage: 93% · resets in 42m
Weekly usage: 23% · resets in 3d 4h
Last usage note: wrap (5-hour) at 2026-11-15T10:22:00.000Z
Usage notes: on
```

The session's refresh receipt gains `usage` (per window, `percent` and
`resets_at_ms`) and `usage_note` (`level`, one of `pace`, `notice` or `wrap`,
with `window` and `at_ms`, or null). It
holds percentages, UTC times and the note's level and window, never its text.
Usage notes need Claude Code's session usage call, tested on 2.1.325. If the
call is missing or fails, keep-warm works as before and the panel reports notes
as unavailable.

Not promised:

- The figures are Claude Code's estimates from the last response; neosian does
  not check them.
- A session learns usage only from its own requests. One that has been idle
  shows a stale figure, missing other sessions' usage, until its next request.
- A burst from many agents can outrun the note and reach the limit before any
  note is delivered.
- Accounts without a 5-hour window get weekly notes only. Sessions without
  subscription figures get none, and other limits, such as a spend limit,
  produce no note.
- An older Claude Code without the usage call keeps keep-warm working without
  notes.
- A note is context, not control: the agent may not follow it.
- No quota saving is guaranteed.

#### Waiting for a reset

Keep-warm also watches the limit, so a session that stopped cleanly near it can
wait for the reset with its cache warm instead of spending the last of the
limit.

Right after each verified refresh the plugin takes one more reading. The rise
of each window's figure since its previous reading is recorded as that
refresh's cost, never below zero, and the last three are kept per window.
Because an idle session's figure is stale, that rise also includes every other
session's usage in between. The estimate used is the smallest of the last
three, between 0.1 and 2 points, or 0.5 before any refresh has been measured.

While keep-warm can refresh in this session (it is on, the session is
interactive, the observation bridge is set up, and the conversation's last
cache observation was a one-hour cache eligible for refresh: a subscription
cache, or any one-hour cache with `allowApi` or an explicit `on`) and a
window's reset time is known and within the wait horizon, the plugin keeps a reserve for the refreshes
until then: the estimate times the number of 55-minute intervals left, at most
5 points. The wrap-up note then comes at 95% less that reserve, or when 10
minutes or less remain at the recent pace before 100% less it; the note still
states the minutes to the limit itself, and the notice stays at 85%. A window
that resets beyond the horizon or at an unknown time keeps no reserve. In `-p`
runs, without the bridge, with keep-warm off, with a five-minute cache, and
before a conversation's first observed reply, the thresholds are exactly those
in the table above.

Just before a refresh would be sent, keep-warm checks the last recorded figures
(an idle session has no newer ones) and sends nothing when:

| Last recorded figure | Reason in `/neosian-cache` |
|---|---|
| Any window at 99.5% or more | Usage limit reached · keep-warm paused |
| A window at 95% or more whose reset is unknown or beyond the horizon | Usage nearly exhausted · keep-warm paused to preserve it |

A window whose reset time has passed never stops a refresh. In every other case
keep-warm refreshes exactly as before, so with a near reset it keeps the cache
warm while you wait. A refresh that Claude Code refuses for a rate limit (its
`rate_limit` error kind or HTTP status 429) shows "Request refused for a rate
limit · keep-warm paused" instead of the general failure reason; that refusal
may come from a short API rate limit rather than the usage limit. As with any other
pause, your next ordinary message rearms keep-warm. These pauses also apply
with `usageNotes` off; only the waiting line below needs a delivered note.

After a wrap-up note in the main conversation, while the session is idle,
keep-warm is maintaining the cache, that window's reset is within the horizon
and the next refresh would be sent, the neosian line reads `waiting for reset`
in place of `keep-warm on`. It does not when the checks above would hold the
next refresh back (for any window), or when a `/neosian-cache` duration ends
before the reset; the line then shows its usual label. `/neosian-cache` adds
the reset and, after a measured refresh, its cost:

```text
Waiting for the 5-hour reset in 42m; keep-warm continues
Refresh moved 5-hour usage by at most 0.3 points
```

Nothing is scheduled or submitted. Continuing after the reset is up to the
agent and you, with Claude Code's own scheduler, and Claude Code's own
wait-and-continue still applies when a limit is reached anyway. The refresh
receipt's `last_refresh` gains `usage_cost`: the points per window
(`five_hour`, `seven_day`), or null for a window when the refresh was not
verified, no reading was possible, or the window is new since the previous
reading or missing from it.

Not promised for the warm wait:

- A refresh's cost is an upper bound, not a measurement of the refresh alone:
  it includes every other session's usage since the previous reading, and the
  reserve built from it is an estimate.
- The figures are stale while the session is idle, so keep-warm decides on the
  last figures the session saw; usage elsewhere can reach the limit first.
- Each refresh while waiting still uses some of the limit. The reserve leaves
  room for it but cannot guarantee that the limit is not reached.
- The cost measurement assumes that Claude Code updates its usage figures from
  the refresh's own response. That has not been verified in a live session; if
  it does not, the cost reads 0.
- No quota saving is guaranteed.

### Standalone cache display

`neosian statusline --client claude-code` also works independently: read
Claude's documented statusline JSON from stdin and print a compact badge, or
use `--json` for normalized fields. Without the installer-owned `--bridge DIR`,
it stores nothing and makes no model or network request. For example:

```text
cache estimate warm ~4m59s | last main read 17633, wrote 637 | misses 0 | cold rebuild ~18272 tokens
```

The cold rebuild count estimates context tokens, not money. Read/write counts
and misses describe main requests; side requests may have a different TTL.
Invalid JSON or input larger than 1 MiB exits 1 without echoing the payload.
See Claude's [statusline documentation](https://code.claude.com/docs/en/statusline)
and [mod API](https://code.claude.com/docs/en/plugins/mods/api).

## What a span becomes

Native memory files are separate from conversation history. With an explicit
`native-memory add ... --write` registration, Claude Code and Codex hooks also
capture selected Markdown into guarded local snapshots and remind the agent to
use neosian memory. Ordinary setup leaves this off. Read
`neosian docs native-memory` for source selection, consent, limits and recovery.

| hook | the record |
|---|---|
| `UserPromptSubmit` | the `USER` message that opens the turn (spooled) |
| `PostToolUse` | an `ASSISTANT` tool call + its `TOOL` result, whole up to 1 MiB (its head and tail above that); a subagent's rounds (`agent_id`) are skipped |
| `Stop` | the final `ASSISTANT` text (`last_assistant_message`); the span lands as one `append_turn` by `claude-code:<session_id>`, and the sessions document is written by `claude-code:<session_id>#<turn>` |
| `SessionStart` | prints the context (below), which the client adds to the model's window; an enabled native-memory registration can capture files first |

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
except on `SessionStart` and supported context-bearing responses, including
native-memory reminders. Cursor uses JSON hook responses, described below.

## Session start: where we left off

Startup injection remains a bounded index. The separate `search_memory`
tool searches current permitted documents when a filename or the initial
index is insufficient, including documents added during the session. It
returns excerpts and source/version references for the agent to read. It
does not rebuild the host's prompt or manage its context cache.

Claude Code, Codex, Cursor and Muse Code add a `SessionStart` hook's
output to the model's context, so on that event the verb prints three
blocks. Without an enabled native-memory registration, it writes nothing:

1. **The memory index** of the scope, so the sessions documents are
   listed (the MCP server's instructions then carry the write discipline
   alone: one window, one copy).
2. **The handoff note**, when one is pending (DESIGN §33): the note the
   last departing agent wrote with `handoff`, in full, naming the
   `continue_session` call that picks it up; one line once it is a week
   old; nothing once a session picked it up.
3. **Where we left off**: the scope's recent sessions, log-projected
   the way a conversation view is (`neosian docs memory`): one line per
   turn, its number in square brackets, newest session first (older
   turns fold into a count line: paging, never deletion). Which sessions
   depends on `source`: after a **compaction** (`source: compact`) the
   session's own record comes back and no note, the re-injection of
   what the client just paged out; on `startup`, `resume`, `clear` or
   `fork` the three most recently written sessions, the own one
   included when it is among them.

The three blocks share one budget, the client's own ceiling on what a
hook may inject (Claude Code 10,000 characters, Codex 2,500 tokens,
Cursor 10,000, Muse Code 16 KiB), counted exactly: the index up to
three eighths of it, the note up to 2048, the sessions the rest, so a
busy scope folds and never trips a client into a file-path preview. A
client with no start hook, OpenCode, reads the same blocks from the MCP
server's instructions, which it shows whole.

A handoff is declared, never assumed. The block offers; a session that
says nothing is new work. When the user says "continue", the agent makes
one call, `continue_session()`, and receives the conversation whole: the
note's session, else the most recent one listed. The block names every
turn it shows, and the footer names the call that re-reads one:
`recall_turn(n, conversation="<id>")` on the `neosian-memory` MCP server
(`neosian docs mcp`), and `search_history(query)` on the same server finds a
turn of any session by its words, listed or not. Bodies stay behind
tools; the block is a table of contents, not the transcript. An empty
scope still prints the frame, so the agent knows the door exists.
For Codex, the text is carried in the hook response's
`hookSpecificOutput.additionalContext` JSON field: its parser treats the
frame's leading `[` as JSON, so a plain-text response would be rejected.
`--json` still selects neosian's diagnostic envelope.
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
neosian audit  # current project scope
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

Switching stores removes only neosian's displaced hooks; other hooks in their
groups stay. `status`
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

## Cursor

`neosian setup --client cursor --url URL --write` installs user MCP at
`~/.cursor/mcp.json` and native version-1 hooks at `~/.cursor/hooks.json`.
Cursor clears custom environment variables for MCP children; the entry
forwards credential names as `${env:NAME}` references, never their values.
Hooks inherit the client environment. The `.cursor` directory must exist.
Both halves are checked before setup writes; unrelated configuration
survives, and reinstall is idempotent.
`record install --client cursor --level project` writes trusted project
hooks instead. Cursor's MCP installer remains user-level only.

User hooks run from `~/.cursor`, so the recorder derives `/project` from
one absolute `workspace_roots` entry in the payload. Missing or multiple
roots use `/user` alone, with a diagnostic. Supply `--project DIR` on the
record command or explicit `--scope`/`--mount` at installation to select a
project; `NEOSIAN_SCOPE` also takes precedence. Tool cwd changes do not
change the session's project.

`beforeSubmitPrompt` supplies the prompt, `postToolUse` the tool round
(including its JSON-string output, whole up to 1 MiB), and
`afterAgentResponse` the assistant text. A completed turn lands once both
`stop` and `afterAgentResponse` have arrived, in either order. Cursor's
interactive CLI can send the stop first. Aborted or failed turns can land
without assistant text. The actor is `cursor:<conversation_id>`.
A per-session advisory spool lock serializes concurrent hook processes
through the store write; it is released if a process exits. Its small
hidden lock file stays in the spool to keep
waiting processes on the same lock. This does not permit multiple direct
writers to a FileStore root.
`sessionStart` returns the same index and recent sessions through JSON
`additional_context`; other successful hooks return `{}`. `--json`
continues to return the recorder's diagnostic envelope.

Cursor can also load Claude Code hooks. The Claude recorder ignores
payloads carrying Cursor's version marker, leaving recording and startup
context to the native Cursor registration. Both configurations stay in
place. This CLI imports Claude configurations containing a matcher;
matcherless groups alone did not load in the probe.

The measured target is the interactive Cursor CLI `2026.09.10-fd3934a`.
Its `--print` mode emitted startup and tool hooks, but omitted prompt,
response and stop events: full recording is unavailable there. Use the
interactive CLI for recording. Transcript parsing is not a fallback.
The installer escapes JSON slashes so this version's comment reader
preserves daemon URLs inside hook commands.

References: Cursor's [hooks](https://cursor.com/docs/hooks) and
[third-party hooks](https://cursor.com/docs/reference/third-party-hooks).

## Pi

`neosian setup --client pi --write` installs both halves for Pi 1.0.1 or
later. The user registration is `~/.pi/agent/mcp.json`, with native
`mcpServers` and direct tool exposure. The recorder is
`~/.pi/agent/extensions/neosian-record.ts`. `PI_CODING_AGENT_DIR` relocates
that agent directory; it must exist before installation. Project level
uses `.pi/mcp.json` and `.pi/extensions/neosian-record.ts` and requires
Pi's project trust. One recording level per client prevents duplicate
spans. Re-running setup replaces our extension and preserves unrelated
MCP entries.

The extension runs in interactive and print modes. It sends actual user
messages, completed tool rounds (including nested MCP calls and error
results), and the final assistant text to `neosian record --agent pi`.
Writes are serialized and each span lands at `agent_settled`, after
automatic retries, rather than the earlier `agent_end`. Orderly shutdown
drains an unfinished span. A killed process may leave a spool for the
next stop; recorder failures never prevent Pi from continuing. The writer
is `pi:<session_id>` and the project comes from Pi's extension context.
No transcript files are parsed.

Pi does not show a server's full instructions automatically. The
extension reads the memory instructions and three startup blocks into a
named `neosian` system-prompt section, preserving other sections. It
refreshes after compaction, including a retry without a new user prompt. The section's
10,000-character budget is neosian's conservative policy, not a claimed
Pi limit. Resume, new-session and fork events reset the context and
recording identity. Profile capture can be paused with `record pause` using
the mapped ID from Pi’s neosian section or OpenCode’s session context.
Pi reads `AGENTS.md` natively.

For shared recording, use `neosian setup --client pi --url URL --write`
and set `NEOSIAN_CLIENT_TOKEN` in Pi's environment; the token is inherited
by both subprocesses and never written to either configuration file.
The extension requires Pi's built-in MCP support; an extension replacing
`/mcp` can disable the native registration. Check `pi mcp list` and
`neosian status` when diagnosing setup.

Validation: Pi 1.0.1 passed a real Claude Code → Pi → Claude Code → Pi
walkthrough on 2026-10-03 with Sonnet 5. Each arrival called
`continue_session`, all three links landed in the recorded sessions,
and the final handoff linked to the last Pi session. The shared memory
instructions require the continue call even when a startup summary
already contains the answer: reading it alone does not pick up the note
or declare the continuation. A separate keyless runtime check exercises
Pi's event pipeline; the adapter checks cover compaction and shutdown.

References: Pi's [configuration](https://pi.dev/docs/latest/configuration),
[MCP](https://pi.dev/docs/latest/mcp) and
[extensions](https://pi.dev/docs/latest/extensions).

## The client table

A row exists only while its walkthrough is green on a real install. "Per
machine" is the default registration: one run of `neosian setup --write`,
two project directories, each session in its own `proj:` scope.

| client | memory (`mcp install`) | record (`record install`) | per machine | session start |
|---|---|---|---|---|
| Claude Code | ✓ user scope through `claude mcp add-json` | ✓ walkthrough green 2026-09-02; headless `claude -p` denies the MCP tools unless `--allowedTools mcp__neosian-memory__…` names them (the prompt before that variadic list) | ✓ 2026-09-19 (2.1.278): the server is spawned in the session's directory, the hook line's project directory expands | ✓ `SessionStart`, stdout as context (2026-09-03); the handoff run for real 2026-10-03 (2.1.288) |
| Codex | ✓ through `codex mcp add` | ✓ walkthrough green 2026-09-03 (`codex exec`) | ✓ 2026-09-19 (0.154.0): the same, user-level hooks with no project trust step | ✓ JSON `additionalContext`; cross-client recall verified on 0.157.1 (2026-09-27) |
| OpenCode | ✓ (`opencode.jsonc` when it holds plain JSON, else `opencode.json`) | ✓ walkthrough green 2026-09-03 (`opencode run`, a plugin); the 2.0 definition verified 2026-10-17 (2.0.25, which refuses the 1.x export) | ✓ 2026-09-19 (1.18.30, a free model): the same, the plugin passing the directory it was opened with; 2026-10-17 (2.0.25) through the binary | ✓ the server's instructions (shown whole, 1.18.30+): the index, the note and the sessions per connection; no hook |
| Claude Desktop | ✓ | no hooks surface | one file by nature; no project, so `/user` alone | — |
| Cursor | ✓ user MCP, credential names forwarded | ✓ interactive CLI 2026.09.10-fd3934a; `--print` lacks full recording; MCP rounds through `afterMCPExecution` | ✓ one user registration, payload workspace roots | ✓ `sessionStart`, JSON `additional_context` |
| Muse Code | ✓ user settings or shared project `.mcp.json` | ✓ walkthrough green 2026-09-22 (`muse exec`, 1.3.0); headless `muse exec` can stall on a shell approval no one can give, so a handoff leg says "no shell commands" | ✓ two projects, one user registration; authenticated managed hooks and MCP on the state process | ✓ `SessionStart`, plain stdout; prior turn recalled over MCP; continued and handed off for real 2026-10-03 (1.4.2) |
| Pi | ✓ native MCP, direct tools (1.0.1) | ✓ print-mode walkthrough green 2026-10-03; extension commits at final settlement | ✓ two projects through one user registration and the state process; `PI_CODING_AGENT_DIR` honored | ✓ named system section, startup and compaction; Claude Code → Pi → Claude Code → Pi with all continuation links verified |

## Durable messages and reminders

See `neosian docs messaging` for the shared `messages` tool, conversation and
scope destinations, history annotations, explicit acknowledgment and claims.
Reminders can be snoozed with a finding and a new due time on the same ID.
`neosian update` regenerates installed client adapters for automatic delivery;
after a package-manager upgrade, run `neosian setup --refresh`. Pi and OpenCode
also support wake requests while attached.
