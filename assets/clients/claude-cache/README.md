# neosian cache maintenance

This plugin uses the standard Claude Code mod API. Install it with
`neosian setup --client claude-code --write`; setup supplies a local observation
bridge and preserves your existing statusline output. Tested with Claude Code
2.1.314. Normal `claude` launches and updates continue to work.

After a successful main request, an observed one-hour subscription cache is
refreshed near minute 55 from the request start, provided the session is idle.
A verified refresh renews that deadline. Normal activity postpones maintenance. Expired,
uncertain, changed or failed caches wait for your next ordinary message. No
automatic cold rebuild, conversation message, tool execution or memory mutation
is requested. The feature remains enabled until you close the session.

A refresh Claude Code does not answer at all (an API error, an abort or
nothing to fork, with no tokens counted) is retried once, 45 to 75 seconds
later, if the session is still idle, keep-warm was not turned off in between,
the model is unchanged, the usage checks below still allow it and the cache
estimate has more than its 30-second margin left. A retry that verifies
continues as a normal refresh; a second failure pauses as before. A refresh
refused for a rate limit, or one that counted tokens without verifying, is
never retried. At most one retry follows each warm cache estimate. After a
compaction the line reads `context compacted`, adding
`next message writes 16k+ tokens` when Claude Code counts the compacted
conversation: that message writes at least that much. No request follows a
compaction.

The compact neosian line shows estimated warmth and keep-warm state above the
prompt. Healthy caches have no warning icon; amber indicates a paused or
uncertain state. Narrow terminals wrap the line instead of truncating it.
`/neosian-cache` opens a dismissible details panel with actual refresh token
counts and, for a refresh Claude Code did not answer, its reason, error kind and
HTTP status, outside the conversation; `off`, `on`, and a duration such as `2h` control
this session. `/config` holds persistent plugin preferences. An explicit `on`
or duration also permits usage-billed sessions with an observed one-hour cache.
Disabling stops future refreshes; an already-sent request may finish.

Usage notes tell the agent, once per limit window, that a 5-hour or weekly
usage limit is near: a notice at 85%, and a wrap-up note at 95% or when about
ten minutes remain at the recent pace. While keep-warm can refresh (it is on
and the conversation's last observed cache was an eligible one-hour cache) and
the window resets within the wait horizon, the wrap-up comes up to 5 points
earlier, leaving a reserve for the refreshes until the reset; a window that has
had its wrap-up stays there until it resets. The wrap-up note asks it to finish the
edit in progress, start nothing new and say what is done and what remains;
subagents receive only that note. The figures are Claude Code's own estimates
from the session's last response. The plugin never submits a prompt, stops a
turn or schedules anything. `usageNotes` in `/config` turns notes off. Notes
use Claude Code's session usage call and are tested on 2.1.325; a Claude Code
without that call keeps keep-warm unchanged and reports notes as unavailable.

Keep-warm waits for a reset warm. It sends no refresh when the last recorded
figure of a window is at 99.5% or more, or at 95% or more with a reset beyond
the horizon or unknown; a window past its reset never stops one. A refresh the
host refuses for a rate limit says so in the panel. After a wrap-up note, an
idle session kept warm with the reset near shows `waiting for reset` while the
next refresh would be sent, and the panel shows the reset time and how far the
last refresh moved each window's figure. That figure is an upper bound: it includes other sessions' usage since
the previous reading, which is stale while the session is idle. A refresh
still uses some of the limit, and nothing is scheduled or submitted.

The host's status is an estimate, not a server cache probe. A cache miss can
only be detected after a request, and the public fork API has no output-token
cap. Unexpected misses or responses stop further refreshes until normal
activity. No quota saving or latency improvement is promised.
Usage may include reasoning even for a one-word visible reply. Receipt limits
are 256 output, 1,024 uncached input and 2,048 cache-write tokens, at least 90%
prefix reuse, and no more than 2,048 unreused prefix tokens. They are checks
after completion, not request-time token caps.

Diagnostics contain only session identifiers, versions, counts, usage
percentages, UTC times and, for an unanswered refresh, Claude Code's reason,
error kind and HTTP status, never its message.
No prompt, response text, credential or memory document is saved by this plugin.
Requests use the current session's model and billing through Claude Code.
