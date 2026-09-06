# neosian README — a Claude Code session landing as a recorded turn.
# Needs `neosian` ([cli] extra) and `claude` on PATH; a logged-in Claude
# Code. Re-render with `vhs record.tape` (https://github.com/charmbracelet/vhs).
Output record.gif
Output record.mp4
Set Shell bash
Set FontSize 18
Set Width 900
Set Height 330
Set Padding 16
Set TypingSpeed 35ms
Set PlaybackSpeed 1.5

Hide
Type "rm -rf ~/demo/the-app ~/demo/home && mkdir -p ~/demo/the-app ~/demo/home && cd ~/demo/the-app && export NEOSIAN_HOME=~/demo/home && printf '# the-app\n\nA small app that keeps its state in neosian.\n' > README.md && clear"
Enter
Show

Type "neosian record install --client claude-code --write"
Enter
Sleep 1.5s
Type `claude -p "Read README.md and tell me its title in one short sentence." --allowedTools Read`
Enter
Wait+Screen@30s /title is/
Sleep 1s
Type "neosian audit --scope user:burakcivitcioglu/proj:the-app"
Enter
Sleep 3s
