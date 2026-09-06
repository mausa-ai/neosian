# neosian README — the next Claude Code session opens on "where we left
# off": the SessionStart hook prints the scope's index and the recorded
# sessions, and the model answers from them. The hidden preamble records
# one session first. Needs `neosian` ([cli] extra) and `claude` on PATH;
# a logged-in Claude Code. Re-render with `vhs left-off.tape`.
Output left-off.gif
Output left-off.mp4
Set Shell bash
Set FontSize 18
Set Width 900
Set Height 200
Set Padding 16
Set TypingSpeed 35ms
Set PlaybackSpeed 1.5

Hide
Type "rm -rf ~/demo/the-app ~/demo/home && mkdir -p ~/demo/the-app ~/demo/home && cd ~/demo/the-app && export NEOSIAN_HOME=~/demo/home && printf '# the-app\n\nA small app that keeps its state in neosian.\n' > README.md && neosian record install --client claude-code --write 2>/dev/null"
Enter
Type `claude -p "Read README.md and tell me its title in one short sentence." --allowedTools Read && clear`
Enter
Wait+Line@60s /^>\s*$/
Sleep 500ms
Show

Type `claude -p "Where did we leave off? Answer in one sentence."`
Enter
Sleep 1s
Wait+Line@30s /^>\s*$/
Sleep 3s
