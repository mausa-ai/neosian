#!/usr/bin/env bash
# neosian installer — uv does the platform work; this script does four
# things, says what lands first, and prints one next step (DESIGN §28,
# §29.10):
#
#   1. uv present, or installed from its pinned release installer
#   2. uv tool install "neosian==<release>"   (a versioned root, one PATH link)
#   3. the PATH check
#   4. the registration command a user came for
#
# Public form (served by neosian.com at /install once published):
#     curl -fsS https://neosian.com/install | bash
# The same two commands, spelled out:
#     curl -LsSf https://astral.sh/uv/<UV_VERSION>/install.sh | sh
#     uv tool install "neosian==<the release this script shipped with>"
# CI form — the wheel built in the same run instead of the index:
#     bash scripts/install.sh --find-links DIR
#
# No sudo, no root writes, no binary matrix, no unpinned upstream.
set -euo pipefail

UV_VERSION="0.12.10"           # the same pin as the Dockerfile
UV_INSTALLER="https://astral.sh/uv/${UV_VERSION}/install.sh"
PACKAGE="neosian"
# The release this script shipped with — the default pin. A unit test keeps
# it equal to pyproject's version; the site serves the script from master.
# Explicit because uv refuses an unpinned pre-release while any final
# release exists on the index, yanked or not (ledger #205).
NEOSIAN_RELEASE="1.0.0rc6"

find_links="${NEOSIAN_INSTALL_FIND_LINKS:-}"
version="${NEOSIAN_VERSION:-$NEOSIAN_RELEASE}"

usage() {
    cat <<USAGE
usage: install.sh [--find-links DIR] [--version X.Y.Z]

  --find-links DIR   install the wheel found in DIR (CI, a local build);
                     the default source is the package index
  --version X.Y.Z    pin the neosian version (default: ${NEOSIAN_RELEASE},
                     the release this script shipped with)

Environment: NEOSIAN_INSTALL_FIND_LINKS, NEOSIAN_VERSION mirror the flags.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --find-links) find_links="$2"; shift 2 ;;
        --version) version="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

ok()   { printf '\342\234\223 %s\n' "$*"; }
fail() { printf '\342\234\227 %s\n' "$*" >&2; exit 1; }

# --- 0. the platform line -------------------------------------------------
os="$(uname -s)"
arch="$(uname -m)"
case "$os" in
    Linux|Darwin) ok "platform: $os $arch, bash ${BASH_VERSION%%(*}" ;;
    MINGW*|MSYS*|CYGWIN*)
        fail "Windows: run the two commands in PowerShell instead —" \
             "'powershell -c \"irm https://astral.sh/uv/${UV_VERSION}/install.ps1 | iex\"'" \
             "then 'uv tool install \"${PACKAGE}==${version}\"'" ;;
    *) fail "unsupported platform: $os $arch" ;;
esac

# --- 1. uv ------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
    ok "uv present: $(uv --version)"
else
    command -v curl >/dev/null 2>&1 || fail "curl is required to fetch uv ${UV_VERSION}"
    echo "  uv not found — installing uv ${UV_VERSION} from ${UV_INSTALLER}"
    curl -LsSf "$UV_INSTALLER" | sh
    # uv's installer lands in ~/.local/bin (or $UV_INSTALL_DIR); make the
    # rest of this run see it even before the shell rc is re-read.
    export PATH="${UV_INSTALL_DIR:-$HOME/.local/bin}:$PATH"
    command -v uv >/dev/null 2>&1 || fail "uv installed but not on PATH; open a new shell and re-run"
    ok "uv installed: $(uv --version)"
fi

# --- 2. the package ---------------------------------------------------------
# What lands, said before it does: a wheel cannot speak during an install.
cat <<BRINGS
  neosian ${version} is one package, about 70 MB on disk:
    the library and its provider SDKs (OpenAI, Anthropic, Cerebras; xAI and
    Gemini through the OpenAI wire), the neosian shell, the MCP server and
    client, the state process, and OpenTelemetry spans. Nothing in it needs
    a key to start. The one extra is the Postgres driver, for a PostgresStore
    against a server you run:  uv tool install "neosian[postgres]"
BRINGS
spec="${PACKAGE}==${version}"
if [ -n "$find_links" ]; then
    [ -d "$find_links" ] || fail "--find-links: not a directory: $find_links"
    uv tool install --python ">=3.12" --find-links "$find_links" "$spec"
    ok "installed $spec from $find_links"
else
    uv tool install --python ">=3.12" "$spec"
    ok "installed $spec"
fi

# --- 3. PATH ----------------------------------------------------------------
if command -v neosian >/dev/null 2>&1; then
    ok "neosian on PATH: neosian $(neosian version | grep -o 'v[0-9][0-9A-Za-z.]*' | head -1)"
else
    bin_dir="$(uv tool dir --bin)"
    echo "  neosian is installed at ${bin_dir} but that directory is not on PATH."
    echo "  Run 'uv tool update-shell', or add this line to your shell rc:"
    echo "      export PATH=\"${bin_dir}:\$PATH\""
    export PATH="${bin_dir}:$PATH"
    command -v neosian >/dev/null 2>&1 || fail "neosian not found on PATH after install"
    ok "neosian installed: neosian $(neosian version | grep -o 'v[0-9][0-9A-Za-z.]*' | head -1) (PATH line above still needed)"
fi

# --- 4. the next step -------------------------------------------------------
cat <<NEXT

Next:
  neosian setup --write    # wire every agent client found: MCP + the ledger's hooks
  neosian status           # is this machine set up (home, keys by name, each client)
  neosian                  # talk to your memory; agents: neosian docs cli --json
NEXT
