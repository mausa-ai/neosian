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
  file or a directory its owner cannot write, a dangling symbolic link, a
  locally edited skill, a statusline without the bridge, or a missing `claude`
  CLI. (`setup --write`, an explicit request, still replaces a read-only file
  and keeps its mode.) Project files are listed, never edited, apart from
  moving neosian's own entry from `neosian-memory` to `neosian` (`neosian docs
  cli`). Only a failed
  write fails a client: Claude's plugin CLI runs before the refresh writes
  anything else for that client, files it wrote are put back unless the client
  changed them meanwhile, every file that still differs is listed, and the exit
  is 1. Run it again to retry or to complete an
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
  hook; our hook is removed from the group that holds it (the group's other
  hooks stay) and a fresh group with it is appended to the event, so a matcher
  on the old group no longer applies to it; a re-run is idempotent. `~/.claude` must exist: neosian never creates another
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
  `[mcp_servers.neosian]` table and the `codex mcp add …` line,
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
  install --client opencode` merges `{"mcp": {"neosian": {"type":
  "local", …}}}` into OpenCode's config in the same directory: into
  `opencode.jsonc` when that file stands there holding plain JSON (the
  file OpenCode's own init leaves), else into `opencode.json`. OpenCode
  reads both files and merges them, the `.jsonc` last, so beside a
  commented `.jsonc` the entry lands in `opencode.json` and the report
  says so (and warns when the `.jsonc` names `neosian` itself,
  since that entry would win). Any model OpenCode can run works, its
  free models included.

## Codex upkeep in the prompt picker

`neosian setup --client codex --write` installs `$neosian-upkeep` alongside
MCP and hooks. `neosian update` keeps an installed entry current unless you
edited it, which the report says, and installs it again if you deleted it while
its receipt still says enabled (use `--codex-skills off` to keep it away); an
installation that predates the skill needs this setup once. Then run
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

If the laptop wakes after expiry, the cache lifetime is unknown, the model or
session configuration changes, a refresh fails verification (an unanswered one
after its retry, below), or a usage limit is reached or nearly exhausted (see
"Waiting for a reset" below), automatic calls stop and the badge warns. The
feature stays enabled: the next successful ordinary message can establish a
new warm cache and rearm maintenance. It does not deliberately rebuild an
expired cache just to produce a small reply. Five-minute caches are observed
without automatic refresh; setup does not change Claude's TTL.

A refresh that Claude Code does not answer at all, with no tokens counted,
after a failure that may clear on its own (a timeout or dropped connection, an
abort, an overload, a server error, or an empty result) is retried once,
at the first 30-second check at least 45 seconds later: 45 to 75 seconds after
it. An abort that comes with your own message is not: the message supersedes
the refresh. The retry is sent only if the session is still idle, keep-warm
was not turned off in between (even if it was turned on again), the model is
unchanged, the usage checks under "Waiting for a reset" still allow a refresh,
and the cache estimate has more than its 30-second margin left; otherwise
keep-warm pauses with the reason that applies. A retry that verifies continues
as a normal refresh, and the next one comes 55 minutes after it. A second
failure pauses as before. A refresh refused for a rate limit, or one that was
answered or counted tokens but did not verify, is never retried: the cache it
found may be gone. Nor is one that sending again cannot fix: failed
authentication or an invalid request (Claude Code's `authentication_failed`
or `invalid_request` kind, or HTTP 400, 401, 403 or 413), a billing error or
an unknown model (kinds Claude Code documents as not clearing on their own),
or nothing to fork. At most one retry follows each warm cache estimate, the
one a message or a verified refresh established. While the retry is due the line
reads `cache ~4m · refresh retry pending` in amber.

A compaction replaces the conversation, so keep-warm waits for your next
message, which writes the compacted conversation to the cache and rearms
maintenance. No request is made after a compaction. The line reads
`context compacted · next message writes 16k+ tokens`, in the neutral tone of
`keep-warm ready`, when Claude Code counts the compacted conversation (rounded
down to thousands), and `context compacted` when it does not. The count is a
floor: in measured sessions the next message wrote about one and a half times
as much, the rest being what Claude Code attaches again after a compaction.

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
output tokens, and for a refresh Claude Code did not answer, its reason, error
kind and HTTP status, for example
`Not answered: api-error · overloaded · HTTP 529`; a retry adds
`Retry of an unanswered refresh:` with the first attempt's.
Press Esc to close it; the panel adds nothing to the conversation.
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
The receipt's `last_refresh` holds `attempt` (1, or 2 for a retry), `outcome`
(`reused`, `unanswered` with its retry due, `stopped` or `superseded`),
`unanswered` (null when Claude Code answered, otherwise its `reason`, `error`
kind and HTTP `status`, each null when not given or not a short identifier)
and `first_attempt` (on a retry, the first attempt's `unanswered`).
One observation and one receipt are overwritten per session, and one shared
usage reading, `usage.shared.json`, by every session (see "A current figure
across sessions" below). They are local diagnostics, not memory or an
authority to resume refresh after a restart; old session files may be removed
while Claude is closed, and the shared reading at any time.

### Usage notes before a limit

Claude Code shows you how much of a subscription usage limit is used but never
tells the model. Reaching the limit cuts the turn in progress. The plugin tells
the agent beforehand so it can stop at a clean point.

The figures are Claude Code's own: the percentage used of the account's 5-hour
and weekly windows and each window's reset time, as the session's last response
reported them, or a newer reading another session on this machine took (see
"A current figure across sessions"). The plugin reads them without a model
request when you send a message, when a tool call finishes and every 30 seconds while the keep-warm
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
`usage 87%`, and the figure's age once it is older than ten minutes, as in
`usage 87% (40m ago)`; the line turns amber at the wrap-up level and stays
amber after a wrap-up note until that window resets. `/neosian-cache` lists
each known window with how old its figure is, marking a figure another session
read, the last note delivered with its UTC time, and whether usage notes are
on, off or unavailable:

```text
5-hour usage: 93% · resets in 42m · as of 2m ago (another session)
Weekly usage: 23% · resets in 3d 4h · as of 40m ago
Last usage note: wrap (5-hour) at 2026-11-15T10:22:00.000Z
Usage notes: on
```

An age under a minute reads `as of just now`. Ages are rounded up, as reset
times are, and count from the last reading that showed at least the figure,
so a figure is never shown as newer than it is. After the computer's clock
steps back a minute or more, no reading's time is known: the line reads
`usage 87% (age unknown)` and the panel `as of unknown` until a new reading
arrives. A smaller correction keeps the ages, at most a minute off.

The session's refresh receipt gains `usage` (per window, `percent`,
`resets_at_ms` and `read_at_ms`, the time of the window's last reading) and
`usage_note` (`level`, one of `pace`, `notice` or `wrap`, with `window` and
`at_ms`, or null). It
holds percentages, UTC times and the note's level and window, never its text.
Usage notes need Claude Code's session usage call, tested on 2.1.325. If the
call is missing or fails, keep-warm works as before and the panel reports notes
as unavailable.

#### A current figure across sessions

A session's own figure stands still while it is idle, but the limit is the
account's. Interactive sessions that share an observation directory (one per
Claude configuration directory with a user-level setup, one per project with a
project-level setup) therefore share their newest reading through one file
there, `usage.shared.json`. It holds the time of the reading and, per window,
the percentage and the reset time: no session ID, account identity, model or
content, for example:

```json
{"format":1,"read_at_ms":1795255320000,"usage":{"five_hour":{"percent":92,"resets_at_ms":1795261200000}}}
```

A session writes its own reading there, once, at its next 30-second check
after one of its responses, unless the file already holds a reading as new.
At every check it reads the file. Nothing identifies an account, so reset
times do. Claude Code's reset times fall on ten-minute marks, and the weekly
reset may be the same hour for many accounts, so only the 5-hour window can
tell one account from another: a reading is taken only when both it and the
session know the 5-hour window with a reset still ahead and the two resets
agree (within two minutes, which means the same ten-minute mark), and a weekly
window both know with a reset still ahead agrees too. The weekly window alone
never admits a reading, so an account with only a weekly window does not
share. A session that has had no response of its own yet, or whose own 5-hour
window has passed, takes nothing until its next response.

From a reading of the same account the session takes a window it already
holds, on the same reset, when the reading is newer than its own last reading
of that window and its figure is not lower, and keeps the later of the two
reset times. A shared reading never starts a window, never lowers a figure,
never ends a window earlier, adds no window the session lacks, and a window
without a reset time in it is ignored. A figure taken this way is used like
the session's own by the line, the notes and the checks before a refresh, so
an idle session at 97% by another session's reading does not send a refresh
the check would hold back. No reading is taken while a refresh is in flight,
so a refresh's measured cost never includes itself.

Learning the figure never makes a request: one small read every 30 seconds and
one write per new reading. A file that is not JSON, is half-written, has
another format or any value out of range, is dated in the future, is larger
than 1 KiB, or is a link or a directory is ignored, and the session keeps its
own figures. So is the file when the system cannot say whether it is there
(a failure other than its absence): nothing is then read or written, and
`/neosian-cache` shows `Shared usage figure: unavailable here; this session
shows only its own`. After the clock steps back a minute or more, no shared
reading dated before the step is taken. `-p` runs neither read nor write it.

Not promised:

- The figures are Claude Code's estimates from the last response; neosian does
  not check them.
- An idle session sees usage that another interactive session sharing its
  observation directory has read. Usage spent on another machine, in the
  Claude apps, by `-p` runs, or under another Claude configuration directory
  is not seen until a session here gets its next response; the age on the line
  is what tells you how old the figure is.
- Two accounts whose 5-hour windows reset at the same ten-minute mark and that
  use the same observation directory (a project-level setup used under two
  Claude configuration directories, or a `/login` to another account) are
  taken for one. The figure shown can then be the other account's, raised
  within its window, which can bring a note early or hold a refresh back,
  never send one.
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
of each window's figure since its previous reading (the session's own, or one
another session took) is recorded as that refresh's cost, never below zero,
and the last three are kept per window. That rise also includes every other
session's usage since the previous reading. The estimate used is the smallest
of the last three, between 0.1 and 2 points, or 0.5 before any refresh has
been measured.

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
(the session's own, or a newer one another session took; nothing is fetched)
and sends nothing when:

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
- Keep-warm decides on the newest figures the sessions sharing its observation
  directory saw; usage elsewhere can reach the limit first.
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
| `Stop` | the final `ASSISTANT` text (`last_assistant_message`); the span is closed into the conversation's queue and lands by `claude-code:<session_id>` (inside the stop for a small local turn, else by the lander the stop starts), as records in parts on a store that keeps records (local, or a server that declares turn records) and as one `append_turn` elsewhere, and the sessions document is written by `claude-code:<session_id>#<turn>` |
| `SessionStart` | prints the context (below), which the client adds to the model's window; an enabled native-memory registration can capture files first |

The sessions document lives at `sessions/<session_id>` in the mount at
`/project` (else the first read-write mount) with agent, conversation,
started, last prompt and turn count, so the next agent finds the listing
in its index. On a store that keeps records it also lists what landed:
`- recorded: prompt, reply, tool_call, tool_result`, the kinds of the
records landed in that order (then `system`, `reasoning`, `attachment` and
`extra` when present), and `- size: 812400 bytes`, their UTF-8 bytes as the
sealed turns count them. Both are cumulative from the first landing the
document lists and are carried from its previous version, so no turn is
read again. A turn sent whole, a turn stored without its document, or a
hand-edited pair leaves both lines out of that write; the next landing
starts the count again and names its turn (`- size: 1024 bytes since turn
7`). An erasure that covers a counted turn removes both lines, and a store
without records writes neither. Between the prompt and the stop the span waits in a
per-session spool (`spool/` under the home; `--spool DIR`), never the store.

At the stop the span is closed: it moves to `pending/<session_id>/<n>.jsonl`
under the spool with a `<n>.state` file beside it, so the next prompt starts a
new span. A small turn for a local store lands inside the stop: the store is a
FileStore that keeps no records (a profile's only when every source is a local
root), the turn's file holds at most 128 KiB, it is the conversation's only
queued turn, and no lander holds the conversation. With `--json` that stop
answers `recorded`; a landing failure exits 1 and keeps the turn queued. Every
other closed turn is made durable and left to a lander: the stop answers
`pending`, exits 0 and starts one, and sends nothing itself. Once the store
has taken a turn it is never sent again: a sessions document that cannot be
written is reported (by a stop, exit 1) while the other turns still land. A
span that cannot be read fails its stop with exit 1 and the reason, as before,
and is set aside so later turns land. An empty span closes nothing.

The lander is `neosian record-land --home <home>`, a hidden verb the hook
starts detached and never waits for: its standard streams on the null device,
in a session and process group of its own on Unix, with `DETACHED_PROCESS` and
a new process group on Windows, the hook's environment inherited. It lands the
home's queues (or the one `--spool DIR` tree the hook was given) conversation
by conversation, each under its landing lock and in the order its turns
closed, and exits when nothing is landable or after ten minutes, finishing the
part in flight; what is left waits for the next hook event. At most two
landers run per home: `spool/landing/slot-1.lock` and `slot-2.lock` each hold
one lander's pid, and a hook that finds both taken starts none. Stop starts one
for the turn it closed; UserPromptSubmit when its own conversation has a queued
turn; SessionStart when a queued turn waits for a destination its environment
can serve; PostToolUse never. None starts one for a destination that waits
or is blocked short of its six-hour attempt (below). A lander reads the marker `record pause` writes
before each turn, each part, each `append_turn` (a repeat after a
`Retry-After` wait too) and each sessions document, and checks that the turn
it took is still the one on disk: once a conversation is paused it sends no
further part, discards the open draft, removes that turn unless the pause
removed it, writes no sessions document, and lands nothing more of the
conversation. A part already in flight when the pause is given may still be
applied, and a seal in flight finishes its turn, without its sessions
document; the pause does not wait for a lander. A profile turn's next write
also waits for the profile's capture to be on and its policy to be the one
the turn was closed under; otherwise the turn stays queued. A hook whose
`--url` carries userinfo or a query hands that URL to its lander in the
lander's environment, `NEOSIAN_LANDER_URL`, never on its command line, since
the state keeps neither.

- A lander outlives the hook: the client reading the hook's output to its end,
  the hook's process group being killed, and the client exiting right after
  the stop leave it running (measured on macOS with the hook runners'
  handling and with the OpenCode and Pi spawn code). A client that ends every
  process of its session (a Linux systemd scope, `KillUserProcesses=yes`) or a
  Windows job object set to kill on close ends it too; the turn stays queued
  and the next hook event starts another. Survival on Windows, and under
  Cursor and Muse Code, is not measured yet.

- A closed turn's first line is its opening line,
  `{"kind": "span", "opened_at": "...", "nonce": "..."}`, which gives each turn
  its own identity even when two turns hold the same words; the span's bytes
  follow unchanged. `n` ascends in the order turns closed and is unique across
  `pending/` and `held/`. A pause that removes pending turns while a lander
  holds the conversation leaves `pending/<session_id>/.next`, so no turn
  closed later takes a number that lander may still hold.
- The state is JSON: the draft id, `next_seq`, the policy fingerprint (profile
  capture only), the destination, `attempt`, `closed_at`, `bytes`, `agent`,
  `actor`, `session`, `conversation_id`, `scope`, `mount`, `provenance`,
  `parent` and `project`. It holds no content and no secret: a Postgres DSN
  appears only as its SHA-256, a `--url` without its userinfo and query, and
  the URL's credential as the SHA-256 of the URL and token together.
- A closed turn lands only from an environment that names the same
  destination: the same root, the same DSN and schema, or the same URL (a
  trailing `/` aside) and token. A turn closed for one destination or token
  is never sent to another: it waits in `pending/`, and later turns for the
  current destination still land. `neosian record held` lists such a turn
  where its environment does not name the destination; it lands when a hook
  started from an environment with its destination and token starts a
  lander.
- A store that keeps records takes each turn as records written in one
  draft, whatever the turn's size: a FileStore after `neosian records enable`,
  Postgres with records extension 1 or 2, a `--url` whose server declares turn
  records version 1, and a profile whose history source is one of these. The
  draft id is the state's, the parts are at most 983,040 bytes and 2,048
  records each, the first states the turn's total bytes, and the last seals
  the turn, which only then becomes visible. A landing first asks the store
  how far the draft got: a draft sealed by an earlier run (its answer lost) is
  not stored twice, a draft left open is continued, its first part repeating
  the last record the store holds so that the store confirms the draft is this
  turn's, and nothing before that record is sent again; a draft gone after
  recorded progress, or one the store reports expired (`draft_gap`, 410),
  starts again from the first record. A turn whose messages records cannot
  carry (a tool call id or name over 1,024 bytes) lands as one `append_turn`.
  A server without turn records, or one that answers 404 to `write_records`,
  takes each turn as one `append_turn` with the bytes released clients send.
- `held/<session_id>/<n>.jsonl` keeps a turn set aside with its state and a
  `<n>.reason` (`policy_changed`, `unreadable`, `refused`, `mismatch`,
  `actor_changed`). A held turn is never sent by itself and never stops a
  later one. A queued turn that has lost its opening line, its state or its
  length, or whose state's draft id is not the one its opening line gives,
  is held `unreadable` rather than sent; one whose state cannot be
  read starts no lander, and the next lander holds it. A turn the store refuses for
  good is held, its draft discarded first: `refused` for a value (400 or 413
  `value_error`, past `NEOSIAN_MAX_RECORD_TURN_BYTES` for one), any 422, any
  other 4xx, a store fault in another turn of the conversation, and a part
  over the bound. A draft that does not match the turn (`draft_mismatch`,
  `draft_size`, a draft longer than the turn, a draft of the turn the store
  cannot read) is discarded and the turn written once more under a fresh
  draft id; refused again, it is held `mismatch`. A draft opened under another
  actor holds the turn `actor_changed`. Its reason file holds the status and
  code the store answered (null when the refusal is neosian's own) and the
  store's or the packer's message, its first 512 characters, with the
  client's own token or credential replaced by `[credential]` wherever it
  appears. A code outside the error registry's grammar (a lowercase letter,
  then lowercase letters, digits and underscores, at most 64 in all), or one
  that holds the credential, is kept and listed as `unknown`. A 2xx to
  `append_turn` that cannot be read, or whose body breaks off, may have
  stored the turn, so the turn is held `unreadable` with "a 2xx was
  answered but could not be read; it may be stored" and
  `"may_be_stored": true` in its reason file, and is neither removed nor
  sent again by itself. When a stop lands its own turn inside it and that
  turn is held, the stop exits 1 with "the turn is held and will not be sent
  again" and a hint naming the file under `held/`.
- `neosian record held [--json] [--spool DIR]` lists the held turns of the
  home's `spool/` and `profile-spool/`, or of `--spool DIR` alone: each
  turn's ID, `<session_id>/<n>`, when it closed, its bytes, its reason with
  the status and code answered and the message its reason file keeps, and its
  destination (a root, a schema, a URL's host and port or a profile's name,
  never a credential). Apart from them it lists the pending turns this
  environment does not serve, which land when a hook from an environment
  that names their destination starts a lander. It reads the turns' state
  and reason files and their lengths, never their content. `--discard ID`
  deletes one held turn and `--discard --all` every one, naming each with its
  bytes, then the turns and bytes deleted. `--retry ID` puts one back at the
  end of its conversation's queue and `--retry --all` every one, a
  conversation's turns in the order they closed. Nothing is sent then: the
  next hook event that starts a lander for the turn's destination lands it.
  A turn put back keeps its bytes, its `closed_at`, its draft id and its
  progress, and takes its conversation's next number, never one a lander may
  hold, so it lands after the turns queued before it. Its reason file goes
  with it into `pending/`, where it tells the keep limit that the turn's
  number does not follow when it closed: the keep limit still deletes it by
  its `closed_at`. It goes back only to the destination and
  the policy it was closed under: a profile turn while its profile, found
  from this home with its capture on, has the same fingerprint, and a turn
  held `policy_changed` once the profile a hook in this directory selects
  (or `--profile NAME`) recorded that conversation under the same policy,
  which then gives the turn its destination, scope, mount, provenance and
  project. A turn of a paused conversation, a turn whose state, length,
  opening line or draft id does not hold, and a turn whose conversation
  another process holds stay held. A turn held because a 2xx could not be
  read (marked `may_be_stored`, or, from an older lander, with that
  sentence alone) goes back only with `--allow-duplicate`, since the store
  may then hold it twice. A retry takes the session lock the turn's
  recorder takes at a stop: the session ID's in `spool/`, the conversation
  key's in `profile-spool/`, and both in a tree `--spool` names. An ID held in both trees is refused until `--spool` names
  one. With `--json` a listing prints `{"held": [...], "unserved": [...]}`,
  each entry with `id`, `spool`, `closed_at`, `bytes` and `destination`, and
  a held one with `reason`, `status`, `code` and `message`; a discard or a
  retry prints `{"action", "turns": [{"id", "spool", "outcome", "bytes",
  "queued", "why"}], "done": {"turns", "bytes"}}`, where `outcome` is
  `discarded`, `retried`, or why the turn stays held: `busy`, `paused`,
  `damaged`, `may_be_stored`, `policy_differs`, `gone` or `failed`. It exits
  0 when every turn named did what was asked, 1 when one stays held or the
  command fails, and 2 for a usage error.
- An answer that will pass by itself leaves the turn queued, and nothing
  later in its conversation lands before it: 16 drafts already open (that
  conversation waits; others land), and for the whole destination the
  answers below. A 429 with a `Retry-After` of at most 10 seconds is waited
  out and the part asked again, at most 30 seconds of waiting per lander;
  past that, and for a longer or absent `Retry-After` (60 seconds), the
  destination waits, timed from when that answer arrived. A 5xx, no answer, an unreadable answer to
  `write_records` (the next landing asks the store how far the draft got),
  `agent_configuration_error` from a store that still declares turn
  records, and a store that cannot be reached make it wait 60 seconds. A
  413 `storage_exceeded`, a 403, a redirect, and a 404 to `append_turn`
  block it; a 401, a handshake that refuses the credential, and a
  credential that cannot be read block it until a sign-in. A lost answer to
  `append_turn` sends the turn again at the next landing, as released
  clients do; a stop landing inside it exits 1 with the answer.
- A destination that waits or is blocked has a record,
  `spool/landing/<key>.json`, where `<key>` is the first 32 hex digits of
  the SHA-256 of the destination as the state names it. It holds the
  destination's host (a URL's host and port, never its userinfo, query or
  token), `wait_until`, `block` (`reason` `storage`, `forbidden` or
  `credential`, the `status`, `code` and `message` answered, `since` and
  `next_attempt`), `failing_since` (when the current run of faults began,
  kept until a turn lands) and `cause` (what the last answer said); a
  message there, and in the lander's summary, is cleaned as a reason file's
  is. The record goes once none of that is left. No hook starts a lander for such a
  destination and no lander sends to it; new turns keep closing into the
  queue. A wait ends at its time. A block ends when you run `neosian record
  retry` (which ends waits too), when a refresh or login brings a changed
  machine document (another ETag; a 304 changes nothing), after a
  successful `neosian login` for a credential block, or by one attempt at
  the first hook event six hours after the last refusal arrived: the lander
  that claims it sends one part, and a 200 ends the block and lands the rest,
  while any other answer, to that part or to the probe before it, keeps the
  turn as it was, discards nothing, and starts the six hours again.
- `[record] keep_bytes` in the home's `config.toml` bounds what recording
  keeps on this machine: every open span, pending turn and held turn under
  the home's `spool/` and `profile-spool/` (and a hook's `--spool DIR`),
  counted by the bytes of their files. Unset, it is 536,870,912 (512 MiB);
  set, it accepts 16,777,216 to 68,719,476,736 bytes. Nothing turns it off:
  a larger value raises it. Each record a hook is about to keep is checked
  first: when it would pass the limit, the
  oldest closed turns, pending or held, are deleted in the order they closed
  until it fits. A conversation a lander is sending (its landing lock taken)
  keeps its turns, and when nothing else can go the record is kept anyway.
  Under that lock a turn is deleted only while it is still the turn chosen,
  by its opening line and close time; when a lander landed it and a retry
  put another turn under its number meanwhile, the trees are scanned again.
  An open span that passes the limit by itself is deleted, and the rest of
  that turn is dropped up to the conversation's next prompt, which starts a
  new span; with `--json` such a hook answers `dropped`. Every deletion is
  counted in `spool/landing/drops.json`: `turns`, `bytes` (the turns, a
  deleted span, and the records dropped with it), and `first` and `last`, the
  times of the first and the latest deletion in UTC. A value out of range or
  not an integer refuses the hook event with the range, before its record is
  written; a `config.toml` that is not TOML leaves 512 MiB.
- `neosian status` adds `recording` lines while a destination is blocked,
  waits, fails or has turns kept here, and for the destination a profile
  selected here records to: `recording  <host>  <state>`, then `kept on
  this machine: N turns waiting, SIZE; M held, SIZE (neosian record held);
  limit 512 MiB`, a line when this environment does not serve the
  destination, and for the whole home `deleted to stay within the limit of
  512 MiB: N turns, SIZE (FIRST to LAST)`. The state reads `paused: ...`
  for a block (since when, and what a server that refuses writes said),
  `failing since ...` with the cause, `waiting until ...`, `not served by
  this environment`, `turns are sent from the next hook event`, `nothing
  waiting to be sent` when only held turns are kept, or `nothing waiting`.
  `status --json` carries the same as `recording`: `limit_bytes`;
  `destinations`, each with `host`, `key`, `detail`, `current`, `state`
  (`blocked`, `failing`, `waiting`, `unserved`, `sending`, `held` or
  `clear`), `blocked` (`reason`, `status`, `code`, `message`, `since`,
  `next_attempt`), `wait_until` while it is ahead, `failing_since`,
  `cause`, `served` (null when nothing is pending), and `pending` and
  `held` as `{"turns", "bytes"}`; `unattributed`, the pending and held
  turns whose state names no destination; and `dropped` (`turns`, `bytes`,
  `first`, `last`). A home with nothing kept, recorded or counted prints no
  recording line and has no `recording` key. `status` reads metadata only,
  opens no store and never takes a session-start notice.
- The sessions document names when the session started: the first turn's
  time, read from that turn's header alone on a store that keeps records, or
  from the turn read whole when the store refuses that header (a first turn
  stored whole that records cannot carry).

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
   included when it is among them. On a store that keeps records each
   session is read as light records: the heads its lines show, never
   reasoning, extra data or an attachment body, from its newest turn back
   through at most 4 MiB of stored turns shared by the sessions shown;
   older turns fold into the count line unread, and a turn records cannot
   carry is one line naming the `recall_turn` call that shows it.

The three blocks share one budget, the client's own ceiling on what a
hook may inject (Claude Code 10,000 characters, Codex 2,500 tokens,
Cursor 10,000, Muse Code 16 KiB), counted exactly: the index up to
three eighths of it, the note up to 2048, the sessions the rest, so a
busy scope folds and never trips a client into a file-path preview. A
client with no start hook, OpenCode, reads the same blocks from the MCP
server's instructions, which it shows whole.

A handoff is declared, never assumed. The block offers; a session that
says nothing is new work. When the user says "continue", the agent makes
one call, `continue_session()`, and receives the conversation within a
budget: the note's session, else the most recent one listed, in the
default view, `light`: every user prompt and final answer verbatim, tool
rounds one line each, the older turns one line each. `view="prompts"`
keeps the user prompts alone, `view="calls"` shows every call with its
arguments and each result by its size, and `view="full"` adds each
result's head and tail and stays within 32,768 characters. On a store
that keeps records the call reads only the heads it shows: a prompt or
reply too long to read whole appears as its head and tail around a
pointer to its record, a newest turn longer than 73,728 characters ends
with a line naming the `recall_turn` call that shows the rest, and a turn
stored whole that records cannot carry is one line naming the
`recall_turn` call that shows it. The block names every
turn it shows, and the footer names the call that re-reads one:
`recall_turn(n, conversation="<id>")` on the `neosian` MCP server
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

Recording notices head the block. Each change of recording state is told
once, at the next session start on the machine, as one line under
`[neosian recording notices: tell the user each line below once]`: a
destination blocked (storage full, writes refused, or the sign-in
refused, with the turns this machine keeps for it), the end of a block
that was told, a day of failures, newly held turns, turns kept for a
destination this session's environment does not serve, and turns the keep
limit deleted, counted for the whole home. The lines come from fixed
wording in `assets/prompts/context.yaml` with only a host, counts, sizes,
the limit and held reason keywords filled in; what a server said is never
put there, and `neosian status` shows it. A wait is not told, nor a block
that began and ended between two starts. One start shows at most eight
lines; the rest wait for a later start, as do lines another start is
telling at that moment. What was told is kept in
`spool/landing/notices.ack` (identities and counters, no content) only
after the answer was written out, so a start that fails to write shows the
lines again. The rest of the block keeps to the client's budget less the
lines. When the store cannot be reached and there is something to tell,
the verb exits 0 with the lines alone and writes the failure on stderr;
with nothing to tell it exits 1 as before. Pi's context before each
prompt and mailbox polling never take a notice; Pi's extension keeps the
lines its start was given apart from the startup context and puts them in
the next prompt's context only. OpenCode, which has no start hook, sees
them in `neosian status` only.

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
| Claude Code | ✓ user scope through `claude mcp add-json` | ✓ walkthrough green 2026-09-02; headless `claude -p` denies the MCP tools unless `--allowedTools mcp__neosian__…` names them (`mcp__neosian-memory__…` where the registration is still on the earlier name) (the prompt before that variadic list) | ✓ 2026-09-19 (2.1.278): the server is spawned in the session's directory, the hook line's project directory expands | ✓ `SessionStart`, stdout as context (2026-09-03); the handoff run for real 2026-10-03 (2.1.288) |
| Codex | ✓ through `codex mcp add` | ✓ walkthrough green 2026-09-03 (`codex exec`) | ✓ 2026-09-19 (0.154.0): the same, user-level hooks with no project trust step | ✓ JSON `additionalContext`; cross-client recall verified on 0.157.1 (2026-09-27) |
| OpenCode | ✓ (`opencode.jsonc` when it holds plain JSON, else `opencode.json`) | ✓ walkthrough green 2026-09-03 (`opencode run`, a plugin); the 2.0 definition verified 2026-10-17 (2.0.25, which refuses the 1.x export) | ✓ 2026-09-19 (1.18.30, a free model): the same, the plugin passing the directory it was opened with; 2026-10-17 (2.0.25) through the binary | ✓ the server's instructions (shown whole, 1.18.30+): the index, the note and the sessions per connection; no hook |
| Claude Desktop | ✓ | no hooks surface | one file by nature; no project, so `/user` alone | — |
| Cursor | ✓ user MCP, credential names forwarded | ✓ interactive CLI 2026.09.10-fd3934a; `--print` lacks full recording; MCP rounds through `afterMCPExecution` | ✓ one user registration, payload workspace roots | ✓ `sessionStart`, JSON `additional_context` |
| Muse Code | ✓ user settings or shared project `.mcp.json` | ✓ walkthrough green 2026-09-22 (`muse exec`, 1.3.0); headless `muse exec` can stall on a shell approval no one can give, so a handoff leg says "no shell commands" | ✓ two projects, one user registration; authenticated managed hooks and MCP on the state process | ✓ `SessionStart`, plain stdout; prior turn recalled over MCP; continued and handed off for real 2026-10-03 (1.4.2) |
| Pi | ✓ native MCP, direct tools (1.0.1) | ✓ print-mode walkthrough green 2026-10-03; extension commits at final settlement | ✓ two projects through one user registration and the state process; `PI_CODING_AGENT_DIR` honored | ✓ named system section, startup and compaction; Claude Code → Pi → Claude Code → Pi with all continuation links verified |

## Sessions in another harness

An agent in one client can start, follow, continue and stop a session in
another installed one (Claude Code, Codex or OpenCode) through the `harness`
MCP tool, once `[harness] enabled = true` stands in neosian's `config.toml`.
Each turn is one run of that client's own headless command (`claude -p`,
`codex exec`, `opencode run --standalone`) under its own login and model, in
`read-only` or `write` mode as that client enforces it, in the server's own
directory or one listed in `[harness] directories`. The child's turns are
recorded by its own hooks as described above, and its record hook writes a
`- parent:` line naming the session that started it. Children never get the
tool. Read `neosian docs harness` for the commands, the per-client modes and
what each does not guarantee. The tool is absent on Windows.

## Durable messages and reminders

See `neosian docs messaging` for the shared `messages` tool, conversation and
scope destinations, history annotations, explicit acknowledgment and claims.
Reminders can be snoozed with a finding and a new due time on the same ID.
`neosian update` regenerates installed client adapters for automatic delivery;
after a package-manager upgrade, run `neosian setup --refresh`. Pi and OpenCode
also support wake requests while attached.
