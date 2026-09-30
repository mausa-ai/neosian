# Claude writes a project memory; the index lists it, the shell shows the file.
# Render with make readme-media; isolated homes and real API calls.
Output memory-write.gif
Output memory-write.mp4
Set Shell bash
Set FontFamily "JetBrainsMono Nerd Font"
Set FontSize 17
Set LineHeight 1.25
Set Width 900
Set Height 640
Set Padding 20
Set TypingSpeed 25ms
Set PlaybackSpeed 2
Set Theme { "name": "Calm-Dark", "background": "#1c1917", "foreground": "#e7e5e4", "cursor": "#d6d3d1", "selection": "#78716c", "black": "#000000", "red": "#990000", "green": "#00a600", "yellow": "#999900", "blue": "#0000b2", "magenta": "#b200b2", "cyan": "#00a6b2", "white": "#bfbfbf", "brightBlack": "#666666", "brightRed": "#e50000", "brightGreen": "#00d900", "brightYellow": "#e5e500", "brightBlue": "#0000ff", "brightMagenta": "#e500e5", "brightCyan": "#00e5e5", "brightWhite": "#e5e5e5" }

Hide
Type `source "$NEOSIAN_DEMO_HELPERS" && demo_setup`
Enter
Sleep 500ms
Wait+Line@90s /^\x{F432}\s*$/
Type "clear"
Enter
Sleep 500ms
Show

Type `claude -p "Use the neosian-memory MCP tool to create the memory document /project/deploy with the content: deploy to eu-west-1. Confirm in one line." --allowedTools mcp__neosian-memory__memory`
Enter
Sleep 500ms
Wait+Line@90s /^\x{F432}\s*$/
Sleep 500ms
Type `neosian memory view /`
Enter
Sleep 500ms
Wait+Line@90s /^\x{F432}\s*$/
Sleep 500ms
Type `neosian memory view /project/deploy`
Enter
Sleep 500ms
Wait+Line@90s /^\x{F432}\s*$/
Sleep 3s

Hide
Type `demo_check memory-write`
Enter
Sleep 500ms
Wait+Line@90s /^\x{F432}\s*$/
Sleep 100ms
