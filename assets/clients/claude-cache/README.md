# Neosian cache maintenance

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

The Neosian badge shows estimates and actual refresh token counts.
`/neosian-cache` shows status; `off`, `on`, and a duration such as `2h` control
this session. `/config` holds persistent plugin preferences. An explicit `on`
or duration also permits usage-billed sessions with an observed one-hour cache.
Disabling stops future refreshes; an already-sent request may finish.

The host's status is an estimate, not a server cache probe. A cache miss can
only be detected after a request, and the public fork API has no output-token
cap. Unexpected misses or responses stop further refreshes until normal
activity. No quota saving or latency improvement is promised.
Usage may include reasoning even for a one-word visible reply. Receipt limits
are 256 output, 1,024 uncached input and 2,048 cache-write tokens, at least 90%
prefix reuse, and no more than 2,048 unreused prefix tokens. They are checks
after completion, not request-time token caps.

Diagnostics contain only session identifiers, versions, counts and UTC times.
No prompt, response text, credential or memory document is saved by this plugin.
Requests use the current session's model and billing through Claude Code.
