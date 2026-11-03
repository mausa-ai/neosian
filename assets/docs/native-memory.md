---
title: "Capture native memory files"
summary: Explicit local Claude Code and Codex memory capture, guarded snapshots and hook reminders
---

# Capture native memory files

`neosian native-memory` registers host-owned Markdown files for automatic,
one-way capture into local neosian memory. Claude Code and Codex hooks then
capture changes even when the agent uses a shell or file tool instead of the
neosian MCP memory tool. Other agents with access to that neosian scope can
read and search the copies normally.

This is off until you register a source with `--write`. Ordinary setup and
conversation recording do not authorize reading native memory directories.
Registration selects an explicit file or directory, client, project, local
store and mounted scope. It remains on this machine, outside portable profiles.

## Register a source

First locate the directory that your host actually uses. Claude Code documents
project memory under `~/.claude/projects/<project>/memory/`, with an optional
`autoMemoryDirectory` override. The directory holds `MEMORY.md` and topic files.
See [Claude Code memory](https://code.claude.com/docs/en/memory).

Codex documents local stores under `$CODEX_HOME/memories/` and
`$CODEX_HOME/memories_v2/`, with version selection and migration behavior.
It can generate memories in the background after a session becomes idle.
Choose the Markdown file or directory you intend to share; neosian does not
infer its project from a filename or inspect a host database. See
[Codex memories](https://learn.chatgpt.com/docs/customization/memories).

Run this from the project whose hooks should capture the source, replacing
the example source path with your actual directory:

```sh
neosian native-memory add claude-notes --agent claude-code \
  --source /absolute/path/to/claude/memory --to /project
```

The preview lists the local binding and each eligible file's destination and
byte count, without printing its body. Repeat with `--write` to authorize
future capture. Registration itself writes no memory documents; the next
hook captures them. To inspect and capture immediately:

```sh
neosian native-memory add claude-notes --agent claude-code \
  --source /absolute/path/to/claude/memory --to /project --write
neosian native-memory sync claude-notes
neosian native-memory sync claude-notes --write
neosian memory view /project/native-memory/claude-notes
```

`--to` names a mount, not an arbitrary document. Copies live at
`/MOUNT/native-memory/NAME/RELATIVE-FILENAME`, keeping the `.md` suffix.
`--root`, `--scope` and `--mount` select storage and authority as in the
memory CLI. Supply the same selectors for explicit `sync`. With `--scope`,
use `--to /memories`. `--project DIR` selects the project used for default
mounts and hook matching; otherwise the current directory is used. Hooks
from that directory or its descendants still need the same mounted scope
and store. An explicit `sync` uses its invocation's mounted authority.

For a Codex source intended to be available across projects, choose the user
mount deliberately:

```sh
neosian native-memory add codex-notes --agent codex \
  --source /absolute/path/to/codex/memories --to /user --all-projects
```

Review the preview, then repeat with `--write`. Without `--all-projects`, a
registration is restricted to its project. Global native stores can contain
knowledge from several projects; neosian does not classify or reroute it.

Directories include only their immediate, non-hidden `.md` files by default.
Use `--recursive` only when you intend to include Markdown in subdirectories
too, including any supporting evidence stored there. A single `.md` file is
also a valid source. Names use 1–64 ASCII letters, digits, hyphens or underscores.

## Capture and agent feedback

The existing `neosian record` registration scans matching sources on
`SessionStart`, `UserPromptSubmit`, `PostToolUse` and `Stop`. It reads the
registered filesystem paths, without parsing shell commands or trusting a
tool payload to select files. Main-session hooks own capture; subagent tool
events are skipped. Files changed between sessions are caught at the next
start; background changes are caught at the next hook or explicit sync.
There is no daemon, continuous watcher, model call or guarantee of observing
every intermediate write. Changes after the final hook wait for another hook.

New or changed copies are ordinary versioned documents. A small JSON provenance
comment identifies the source label, client and relative filename; the source
body follows verbatim, including any frontmatter. Normal neosian timestamps
record capture time. Hook captures use the observing `<agent>:<session_id>`
actor, which is attribution, not proof of who wrote the source file.

After a capture or refusal on `PostToolUse`, neosian returns a short reminder
through `hookSpecificOutput.additionalContext`. The next prompt also reminds
the agent to use neosian memory for durable facts. Bodies are never injected
by this bridge. Session start uses the existing bounded memory index; stop
does not block or request another model turn. The hosts own delivery and agent
behavior; a reminder cannot force tool use. See the documented
[Claude hooks](https://code.claude.com/docs/en/hooks) and
[Codex hooks](https://learn.chatgpt.com/docs/hooks).

## Guards, limits and recovery

The first release supports local FileStore destinations without a profile.
Profiles, HTTP and direct Postgres selections refuse `add` and `sync`;
profile hooks do not run native capture. Profile capture-off and filtering
rules remain intact. Wire 6 does not provide the atomic create-and-receipt
operation required for safe automatic capture on a remote backend. Use the
separate explicit knowledge-import workflow there, with its paused writers.

Current read-write mounted authority is checked before reading source bytes.
Read-only and edit-only mounts cannot capture. Sources and destination stores
must not overlap. Symlinks, special files, non-UTF-8 input and filenames outside
neosian's path grammar refuse the scan. Files changing during a read are retried
at the next hook. These checks assume cooperating filesystem users; they do
not isolate the bridge from a malicious process running as the same OS user.

There are at most 16 registrations. Each scan permits 256 files, 1 MiB per
file, 8 MiB total, 1,024 traversed entries and eight directory levels. Exceeding
a source budget refuses its whole scan before copying any of it. Split large
sources explicitly. Capture does not detect or scrub secrets; choose sources
appropriate for every reader of the destination scope.

Each local copy, version, change event and receipt commits under the same
FileStore lock and redo transaction. Unchanged content creates no version.
Receipts guard existing versions and content: an occupied destination, missing
or corrupt receipt, independent edit, deletion or erasure refuses replacement.
Existing collection definitions require explicit knowledge import. A conflict
in one file does not discard successful captures of other files; `sync --json`
reports each action. Repeating sync after a lost response first recovers any
committed transaction, then recognizes its receipt without another version.
Inspect refusals instead of deleting receipts or erasure history to bypass them.

An interrupted commit reports `recovery_required` and leaves later files
`not_attempted`. A failed call is not proof that nothing was saved: resolve the
filesystem error, then retry sync to recover the existing transaction and receipt.

Deleting or renaming a source does not remove earlier neosian copies. Source
files stay untouched, and deleting a neosian copy does not delete the source.
To forget information, stop its registration and erase each retained copy and
backup explicitly. Curate durable facts into other neosian documents rather
than editing snapshots you still want the bridge to update.

```sh
neosian native-memory list --json
neosian native-memory remove claude-notes
neosian native-memory remove claude-notes --write
```

Registrations live in `<NEOSIAN_HOME>/native-memory.json`; private receipts
live under each selected FileStore scope's `.native-memory/` directory.
Portable archives carry the ordinary documents and their history, but exclude
these local bindings and receipts. Restored copies remain inspectable and
are not silently adopted by a new registration. Upgrade every writer before
enabling capture: older writers cannot recover transactions containing the
new private receipt path. Memory format 1, wire 6 and Postgres schema 4 remain
unchanged. No new storage capability is advertised.
