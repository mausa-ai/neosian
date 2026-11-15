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

The compact neosian line shows estimated warmth and keep-warm state above the
prompt. Healthy caches have no warning icon; amber indicates a paused or
uncertain state. Narrow terminals wrap the line instead of truncating it.
`/neosian-cache` opens a dismissible details panel with actual refresh token
counts, outside the conversation; `off`, `on`, and a duration such as `2h` control
this session. `/config` holds persistent plugin preferences. An explicit `on`
or duration also permits usage-billed sessions with an observed one-hour cache.
Disabling stops future refreshes; an already-sent request may finish.

Usage notes tell the agent, once per limit window, that a 5-hour or weekly
usage limit is near: a notice at 85%, and a wrap-up note at 95% or when about
ten minutes remain at the recent pace. The wrap-up note asks it to finish the
edit in progress, start nothing new and say what is done and what remains;
subagents receive only that note. The figures are Claude Code's own estimates
from the session's last response. The plugin never submits a prompt, stops a
turn or schedules anything. `usageNotes` in `/config` turns notes off. Notes
use Claude Code's session usage call and are tested on 2.1.325; a Claude Code
without that call keeps keep-warm unchanged and reports notes as unavailable.

The host's status is an estimate, not a server cache probe. A cache miss can
only be detected after a request, and the public fork API has no output-token
cap. Unexpected misses or responses stop further refreshes until normal
activity. No quota saving or latency improvement is promised.
Usage may include reasoning even for a one-word visible reply. Receipt limits
are 256 output, 1,024 uncached input and 2,048 cache-write tokens, at least 90%
prefix reuse, and no more than 2,048 unreused prefix tokens. They are checks
after completion, not request-time token caps.

Diagnostics contain only session identifiers, versions, counts, usage
percentages and UTC times.
No prompt, response text, credential or memory document is saved by this plugin.
Requests use the current session's model and billing through Claude Code.
