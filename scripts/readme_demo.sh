# Sourced by the tapes inside the temporary homes from readme_media.sh.
# Wrappers select the demo models and keep Codex's diagnostic stream off
# the frame. They preserve real stdout and fail on hook errors.
set -euo pipefail
export PS1='> '
mkdir -p "$NEOSIAN_DEMO_ROOT/the-app"
cd "$NEOSIAN_DEMO_ROOT/the-app"
printf '# the-app\n\nA small app that keeps its state in neosian.\n' > README.md

claude() {
    command claude --model haiku "$@" | tee "$NEOSIAN_DEMO_ROOT/claude-answer"
}

codex() {
    command codex --no-daemon "$1" --model gpt-6-luna \
        --skip-git-repo-check --dangerously-bypass-hook-trust \
        "${@:2}" 2>"$NEOSIAN_DEMO_ROOT/codex.log" \
        | tee "$NEOSIAN_DEMO_ROOT/codex-answer"
    if grep -Eq 'hook: .*Failed|mcp: .*failed' "$NEOSIAN_DEMO_ROOT/codex.log"; then
        cat "$NEOSIAN_DEMO_ROOT/codex.log" >&2
        return 1
    fi
}

demo_setup() {
    neosian setup --client claude-code "$@" --write >"$NEOSIAN_DEMO_ROOT/setup.log" 2>&1
}

demo_codex_login() {
    printenv OPENAI_API_KEY | command codex login --with-api-key \
        >"$NEOSIAN_DEMO_ROOT/login.log" 2>&1
}

demo_check() {
    neosian audit --json >"$NEOSIAN_DEMO_ROOT/audit.json"
    grep -q 'claude-code:' "$NEOSIAN_DEMO_ROOT/audit.json"
    case "$1" in
        record|left-off)
            grep -qi 'the-app' "$NEOSIAN_DEMO_ROOT/claude-answer" ;;
        many-agents)
            grep -q 'codex:' "$NEOSIAN_DEMO_ROOT/audit.json"
            grep -qi 'release' "$NEOSIAN_DEMO_ROOT/codex-answer"
            grep -q 'eu-west-1' "$NEOSIAN_DEMO_ROOT/codex-answer" ;;
        memory-write)
            neosian memory view /project/deploy | grep -q 'eu-west-1' ;;
    esac
    touch "$NEOSIAN_DEMO_ROOT/complete"
}
