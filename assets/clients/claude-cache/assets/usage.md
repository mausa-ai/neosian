---
name: neosian-cache-usage
summary: Usage-limit notes handed to the agent as hook context; guidance only, never a prompt or a block.
---
Each `##` heading names one block. The plugin substitutes the placeholders
`{window}`, `{percent}`, `{reset_in}`, `{reset_at}` and `{minutes_left}`; the
note blocks also take `{reset}`, `{pace}` and `{advice}`, filled from the
blocks of those names. Line breaks inside a block are joined with spaces.

## notice

neosian usage note: this account has used {percent}% of its {window} usage
limit. {reset} {pace} Nothing needs to stop yet. Prefer finishing the work in
progress to starting large new work or launching several agents at once. The
figure is the host's estimate from its last response.

## wrap

neosian usage note: this account has used {percent}% of its {window} usage
limit. {reset} {pace} Requests are refused at 100%, which would cut this turn
off mid-task. Bring the work to a clean stopping point now: finish the edit in
progress, start no new tasks or agents, and say briefly what is done and what
remains. {advice} The figure is the host's estimate from its last response.

## agent

neosian usage note: this account has used {percent}% of its {window} usage
limit. {reset} {pace} Requests are refused at 100%, which would cut this work
off. Finish the step in progress and return now, stating clearly what is done
and what remains.

## pace

At the recent pace it reaches the limit in about {minutes_left}.

## reset

It resets in {reset_in} ({reset_at} UTC).

## noreset

Its reset time is not known.

## wait

If the user wants the remaining work completed, schedule its continuation for
just after the reset with the host's own scheduling tool, then end the turn.

## handoff

Do not wait for the reset. Leave a handoff note that lets the work resume in a
later session.
