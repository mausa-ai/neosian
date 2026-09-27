# Claude writes a project memory; the shell renders the saved markdown.
# Render with make readme-media; isolated homes and real API calls.
Output memory-write.gif
Output memory-write.mp4
Set Shell bash
Set FontSize 18
Set Width 900
Set Height 360
Set Padding 16
Set TypingSpeed 25ms
Set PlaybackSpeed 2

Hide
Type `source "$NEOSIAN_DEMO_HELPERS" && demo_setup`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Type "clear"
Enter
Sleep 500ms
Show

Type `claude -p "Use the neosian-memory MCP tool to save /project/deploy: deploy to eu-west-1. Confirm in one line." --allowedTools mcp__neosian-memory__memory`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 500ms
Type `neosian memory view /project/deploy`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 4s

Hide
Type `demo_check memory-write`
Enter
Sleep 500ms
Wait+Line@90s /^>\s*$/
Sleep 100ms
