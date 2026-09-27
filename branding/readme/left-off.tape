# A fresh Claude Code session recalls the recorded work.
# Render with make readme-media; isolated homes and real API calls.
Output left-off.gif
Output left-off.mp4
Set Shell bash
Set FontSize 18
Set Width 900
Set Height 220
Set Padding 16
Set TypingSpeed 25ms
Set PlaybackSpeed 2

Hide
Type `source "$NEOSIAN_DEMO_HELPERS" && demo_setup`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Type `claude -p "Read README.md. Reply with its exact H1 title, nothing else." --allowedTools Read`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 500ms
Type "clear"
Enter
Sleep 500ms
Show

Type `claude -p "Where did we leave off? Answer in one sentence."`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 4s

Hide
Type `demo_check left-off`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 100ms
