# A Claude Code turn recorded by the hooks and named by the ledger.
# Render with make readme-media; isolated homes and real API calls.
Output record.gif
Output record.mp4
Set Shell bash
Set FontSize 18
Set Width 900
Set Height 420
Set Padding 16
Set TypingSpeed 25ms
Set PlaybackSpeed 2

Hide
Type `source "$NEOSIAN_DEMO_HELPERS"`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Type "clear"
Enter
Sleep 500ms
Show

Type `neosian setup --client claude-code --write`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 500ms
Type `claude -p "Read README.md. Reply with its exact H1 title, nothing else." --allowedTools Read`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 500ms
Type `neosian audit`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 4s

Hide
Type `demo_check record`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 100ms
