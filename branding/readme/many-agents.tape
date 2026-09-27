# One store: Claude Code records a session, then Codex recalls it.
# Render with make readme-media; isolated homes and real API calls.
Output many-agents.gif
Output many-agents.mp4
Set Shell bash
Set FontSize 18
Set Width 900
Set Height 380
Set Padding 16
Set TypingSpeed 25ms
Set PlaybackSpeed 2

Hide
Type `source "$NEOSIAN_DEMO_HELPERS" && demo_codex_login && demo_setup --client codex`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Type "clear"
Enter
Sleep 500ms
Show

Type `claude -p "We deploy the release branch to eu-west-1. Confirm in one line; no tools." --tools ""`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 500ms
Type `codex exec "Where did we leave off? One sentence, from startup context only; no tools."`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 4s

Hide
Type `demo_check many-agents`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 100ms
