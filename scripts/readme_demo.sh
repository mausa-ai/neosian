# Sourced by the tapes inside the temporary homes from readme_media.sh.
# The prompt is the maintainer's terminal (branding/readme/starship.toml);
# wrappers select the demo models and keep Codex's diagnostic stream off
# the frame. They preserve real stdout and fail on hook errors. No `set -e`:
# the shell must outlive a failing verb so the frame shows it and
# readme_media.sh reports it; an exiting shell leaves an empty screen.
export STARSHIP_CONFIG="${NEOSIAN_DEMO_HELPERS%/scripts/*}/branding/readme/starship.toml"
eval "$(starship init bash)"
mkdir -p "$NEOSIAN_DEMO_ROOT/the-app"
cd "$NEOSIAN_DEMO_ROOT/the-app" || return 1
printf '# the-app\n\nA small app that keeps its state in neosian.\n' > README.md
git init -q -b main
git add README.md
git -c user.name=neosian -c user.email=community@neosian.com commit -qm "the app"

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
    neosian audit --json >"$NEOSIAN_DEMO_ROOT/audit.json" || return 1
    grep -q 'claude-code:' "$NEOSIAN_DEMO_ROOT/audit.json" || return 1
    case "$1" in
        record)
            grep -qi 'the-app' "$NEOSIAN_DEMO_ROOT/claude-answer" || return 1 ;;
        left-off)
            grep -qi 'the-app' "$NEOSIAN_DEMO_ROOT/claude-answer" || return 1
            neosian search "H1 title" --json | grep -q '"snippet"' || return 1 ;;
        many-agents)
            grep -q 'codex:' "$NEOSIAN_DEMO_ROOT/audit.json" || return 1
            grep -qi 'release' "$NEOSIAN_DEMO_ROOT/codex-answer" || return 1
            grep -q 'eu-west-1' "$NEOSIAN_DEMO_ROOT/codex-answer" || return 1 ;;
        memory-write)
            neosian memory view / | grep -q '/project/deploy' || return 1
            neosian memory view /project/deploy | grep -q 'eu-west-1' || return 1 ;;
    esac
    touch "$NEOSIAN_DEMO_ROOT/complete"
}
