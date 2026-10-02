---
description: Hand this session off, so that the next session in any agent continues it; write the note, then stop
---
Write the handoff note now: one call to the `handoff` tool on the
neosian-memory server, the note as its `note` argument, under 2000
characters. Say what the next agent needs, not a summary of the chat:

1. What was being done and for whom.
2. What is done: the file paths, ids and commands that matter.
3. What is next: the first step, precisely.
4. What is open or blocked, and what the user still has to decide.

The record holds every turn of this session, so name a turn as [n]
instead of repeating it. After the call, tell the user in one line that
the note is written and that saying "continue" in any neosian-wired
agent picks it up. Then stop.
