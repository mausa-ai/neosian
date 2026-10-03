---
title: Messages and reminders
summary: Durable session inboxes, history annotations, claims and resettable reminders
---

# Messages and reminders

The `messages` tool is available over MCP. Python applications opt in with
`Conversation(..., mailbox=MailboxConfig())`; import mailbox values from
`neosian.messaging`. The stateless `Agent` owns no inbox or background task.

```python
from neosian import FileStore, MemoryConfig, Mount
from neosian.messaging import Mailbox, MessageTarget

memory = MemoryConfig(FileStore("./state"), (
    Mount("user:me/proj:app", "project"),
    Mount("user:me", "user"),
))
mailbox = Mailbox(memory, session="working-session", actor="agent:worker")
receipt = await mailbox.send(
    MessageTarget("user:me/proj:app"),
    "Check whether the upstream fix has been released; issue #123 blocks us.",
    delay_seconds=15 * 86400,
)
```

## Address and deliver

A destination names a mounted scope and optionally a conversation ID. A
project message reaches project sessions; a user message reaches sessions
mounting that user scope. Conversation messages reach that conversation and
its explicit continuations, including transitive continuations. Unrelated sessions can read
authorized historical annotations without consuming the recipient's inbox.

The recipient need not be running or have recorded its first turn. Sending
confirms durable storage, never that another model read the message. The MCP
server does not infer a conversation from recency: hooks print the exact
session ID, which an unbound caller passes as `session`. This ID is routing
metadata, not authentication; mounts and the host's access controls define
authority. The transport's actor is recorded separately.

`delivery="next_activity"` is the default. The next supported lifecycle
boundary delivers unread items. `delivery="wake"` also requests a turn from
an attached capable receiver. An unavailable receiver leaves the item pending;
neosian never launches a stopped agent. A busy receiver finishes its current
operation. Wake receivers poll every five seconds while attached, including
for reminders that become due. No model checks anything before delivery.

| Integration | Automatic delivery | Idle wake |
|---|---|---|
| Native Conversation | Before each send | Host-owned receiver callback |
| neosian interactive shell | Next submitted turn | No |
| Claude Code | Session start, prompt submission, post-tool | No |
| Codex, Cursor, Muse Code | Session start/resume/compaction | No |
| Pi | Session start and before each agent turn | Custom message, follow-up turn |
| OpenCode | Prompt and tool boundaries | Attached plugin, async session prompt |
| MCP-only client | Explicit tool calls | No |

Re-run `neosian setup --write` to regenerate installed client adapters after
upgrading. Pi receivers stop on session shutdown; OpenCode receivers track
sessions observed by that plugin process, dropping deleted sessions. No
historical session is awakened merely because its record exists.

Context is bounded to 2,048 characters inside the client's existing budget.
Clipped and omitted items remain pending; `messages list/view` reads them.
Messages carry attribution and are data, not elevated user/system instructions.

## Commands and lifecycle

The tool has `send`, `list`, `view`, `ack`, `claim`, `renew`, `release`,
`complete`, `snooze` and `cancel` commands. `scope` accepts a mounted scope or
`/project`/`/user`. `list` accepts `unread`, `open`, `scheduled`, `closed` or
`all`, with a default limit of 50 and maximum 500. `view` includes versions.

Reading or printing context never marks an item read. `ack` explicitly
acknowledges its returned `occurrence`. Until then it reappears at activity
boundaries. Wake acceptance is separate: missing acknowledgment does not
start an endless sequence of autonomous turns.

Immediate messages are informational unless `actionable=true`. Dated
reminders are always actionable. Claim before doing their work. A claim marks
the item read and grants one session a 30-minute renewable lease. The returned
token is required for `renew`, `release`, `complete` and `snooze`. Completion
requires an outcome. Acknowledged but unfinished work remains in `list open`.
Expired claims become unread/claimable again; stale tokens cannot finish work.

```python
item = (await mailbox.list())[0].message
claim = await mailbox.update(item.scope, item.id, "claim", occurrence=item.occurrence)
reset = await mailbox.update(
    item.scope, item.id, "snooze", occurrence=item.occurrence,
    token=claim.message.claim_token,
    delay_seconds=14 * 86400,
    outcome="Checked the release and issue: still no fix.",
)
```

Snoozing retains the same ID and all prior findings in version history. It
releases ownership, advances the occurrence, clears acknowledgment and sets
the next due time. A late acknowledgment from the old occurrence is refused.
There is no delivery before the new time; equality is eligible. Supply either
an aware ISO-8601 `due_at` or positive `delay_seconds`, never both. Times are
stored in UTC, and relative delays start at the operation's clock. There are
no cron expressions or implicit recurring schedules.

The shell uses the same implementation:

```sh
neosian messages send --target /project --body "Check upstream issue #123" --delay-seconds 1296000 --json
neosian messages list --session SESSION --json
neosian messages claim --target /project --session SESSION --message-id UUID --occurrence 1 --json
neosian messages snooze --target /project --session SESSION --message-id UUID --occurrence 1 --token TOKEN --delay-seconds 1209600 --outcome "Still no fix" --json
```

`--scope`/`--mount`, `--root`, `--url` and the existing database/environment
configuration select storage. `--target` selects a destination among mounts.

## History and ownership

Conversation-addressed messages annotate their recipient. `about` and
`about_turn` attach a scope message to historical work without changing its
delivery audience. Search finds message bodies and earlier outcome notes.
Recall appends dated annotations after the original verbatim turn; successful
completion or acknowledgment does not remove a correction. Cancellation is
labelled. Operator redaction clears the message and its versions.

The Python `listen(mailbox, callback)` coroutine is explicitly started and
cancelled by its host. The async callback receives an `InboxMessage`, schedules
the host's turn and returns whether the delivery was accepted. It must
deduplicate `(id, occurrence)` when its transport can accept a request and
lose the response. `receive_once` is the same operation for a host-owned loop.
Delivery is retryable, not exactly-once model execution.

## Recorded design decision: mailbox v1

Messages are versioned documents under reserved `messages/`; immediate
continuation links use `message-links/`. They share audit, export/import and
redaction with memory. Reflection and maintenance exclude these paths, and
ordinary memory-tool mutations refuse them. The operator retains direct
storage, redaction and recovery access. New message creation is not implicitly
retried; repeated sends create separate messages. Lifecycle mutations use
optimistic concurrency, occurrence checks and fenced ownership tokens.

The two existing storage ABCs and HTTP wire remain unchanged. A custom store
must declare cross-worker optimistic concurrency to perform shared lifecycle
updates. Unsupported message formats are reported and never rewritten.

FileStore now serializes cooperating local processes with a stable private
`.store.lock` file. Every writer must use this implementation; old processes
must be restarted together. Locks cover memory, turns, restores and reads of
mutable journals. Cancellation drains worker I/O before releasing the lock;
process death releases the OS lock. The lock file is never unlinked. This is
local filesystem coordination, not a distributed/network-filesystem lock.
Journal-before-document crash behavior is unchanged; cross-file writes are
not transactions. Postgres remains the scalable multi-worker substrate.

No new storage schema, wire version or agent-event vocabulary is introduced.
Mailbox document format is independently versioned. Initial search scans
message documents; it does not add the reserved general memory-search API.
