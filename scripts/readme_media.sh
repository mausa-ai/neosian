#!/usr/bin/env bash
# Render all four tapes with real clients in disposable config homes.
# Keys come only from the caller's environment, never from a named file.
set -euo pipefail
cd "$(dirname "$0")/.."

for tool in vhs ffmpeg ffprobe ttyd starship git claude codex; do
    command -v "$tool" >/dev/null || { echo "Missing tool: $tool"; exit 1; }
done
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY is required}"
: "${OPENAI_API_KEY:?OPENAI_API_KEY is required}"
test -x .venv/bin/neosian || { echo "Run make install first"; exit 1; }
# Capture the rendered terminal views even from a colourless parent process.
unset NO_COLOR

repo=$PWD
export PATH="$repo/.venv/bin:$PATH"
export NEOSIAN_DEMO_HELPERS="$repo/scripts/readme_demo.sh"
render_root=$(mktemp -d "${TMPDIR:-/tmp}/neosian-media.XXXXXX")
trap 'rm -rf "$render_root"' EXIT

for tape in record left-off many-agents memory-write; do
    echo "Rendering $tape.tape"
    export NEOSIAN_DEMO_ROOT="$render_root/$tape"
    mkdir -p "$NEOSIAN_DEMO_ROOT/claude" "$NEOSIAN_DEMO_ROOT/codex"
    (
        cd "$render_root"
        env NEOSIAN_HOME="$NEOSIAN_DEMO_ROOT/home" \
            CLAUDE_CONFIG_DIR="$NEOSIAN_DEMO_ROOT/claude" \
            CODEX_HOME="$NEOSIAN_DEMO_ROOT/codex" \
            vhs "$repo/branding/readme/$tape.tape"
    ) || echo "vhs failed: $tape"
    test -f "$NEOSIAN_DEMO_ROOT/complete" || {
        echo "Recording failed its content checks: $tape"
        for log in setup.log claude-answer codex-answer codex.log audit.json; do
            test -s "$NEOSIAN_DEMO_ROOT/$log" || continue
            echo "--- $log"; tail -c 1200 "$NEOSIAN_DEMO_ROOT/$log"; echo
        done
        exit 1
    }
    for format in gif mp4; do
        seconds=$(ffprobe -v error -show_entries format=duration \
            -of csv=p=0 "$render_root/$tape.$format")
        printf '  %-20s %5.1f s\n' "$tape.$format" "$seconds"
        awk -v seconds="$seconds" 'BEGIN { exit !(seconds > 0 && seconds < 15) }' || {
            echo "Recording must be under 15 seconds: $tape.$format"; exit 1;
        }
    done
done

# Publish only after every recording passed, preserving the previous set on failure.
for tape in record left-off many-agents memory-write; do
    cp "$render_root/$tape.gif" "$render_root/$tape.mp4" "$repo/branding/readme/"
done
