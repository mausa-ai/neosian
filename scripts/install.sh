#!/bin/sh
# The binary by default; --python retains the uv tool installation.
# cargo-dist owns platform selection, archive verification and update receipts.
set -eu

UV_VERSION="0.12.23"           # the same pin as the Python workflows
UV_INSTALLER="https://astral.sh/uv/${UV_VERSION}/install.sh"
PACKAGE="neosian"
# The release this script shipped with — the default pin. A unit test keeps
# it equal to pyproject's version; the site serves the script from master.
# Explicit so the installer resolves the release it describes.
NEOSIAN_RELEASE="1.13.0"

python=0
find_links="${NEOSIAN_INSTALL_FIND_LINKS:-}"
version="${NEOSIAN_VERSION:-$NEOSIAN_RELEASE}"

usage() {
    cat <<USAGE
usage: install.sh [--python] [--find-links DIR] [--version X.Y.Z]

  --python          install the Python shell and SDK through uv
  --find-links DIR   install the Python wheel found in DIR (implies --python);
                     the default source is the package index
  --version X.Y.Z    pin the neosian version (default: ${NEOSIAN_RELEASE},
                     the release this script shipped with)

Environment: NEOSIAN_INSTALL_FIND_LINKS, NEOSIAN_VERSION mirror the flags.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --python) python=1; shift ;;
        --find-links|--version)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                echo "error: $1 needs a value" >&2
                exit 2
            fi
            case "$1" in
                --find-links) find_links="$2" ;;
                --version) version="$2" ;;
            esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "error: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

ok()   { printf '\342\234\223 %s\n' "$*"; }
fail() { printf '\342\234\227 %s\n' "$*" >&2; exit 1; }

# Version enters a URL and a package spec, so accept the release grammar only.
printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(rc[0-9]+)?$' || {
    echo "error: --version must be X.Y.Z or X.Y.ZrcN" >&2; exit 2;
}
[ -z "$find_links" ] || python=1
os="$(uname -s)"
arch="$(uname -m)"
case "$os" in
    Linux|Darwin) ok "platform: $os $arch" ;;
    MINGW*|MSYS*|CYGWIN*)
        fail "Windows: use the PowerShell installer or MSI at https://github.com/mausa-ai/neosian/releases/tag/v${version}" ;;
    *) fail "unsupported platform: $os $arch" ;;
esac

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
trap 'exit 1' HUP INT TERM

if [ "$python" = 0 ]; then
    case "$arch" in
        arm64|aarch64|x86_64|amd64) ;;
        *) fail "unsupported architecture: $arch; use --python" ;;
    esac
    command -v curl >/dev/null 2>&1 || fail "curl is required"
    # cargo-dist 0.32 verifies SHA-256 only with sha256sum. macOS ships
    # shasum instead: provide its equivalent privately for this install.
    if ! command -v sha256sum >/dev/null 2>&1; then
        command -v shasum >/dev/null 2>&1 || fail "sha256sum or shasum is required"
        mkdir "$scratch/bin"
        cat > "$scratch/bin/sha256sum" <<'CHECKSUM'
#!/bin/sh
exec shasum -a 256 "$@"
CHECKSUM
        chmod +x "$scratch/bin/sha256sum"
        export PATH="$scratch/bin:$PATH"
    fi
    echo "  neosian ${version}: the native binary, with no Python or uv required."
    echo "  Chat, playground and eval are available through install.sh --python."
    installer="https://github.com/mausa-ai/neosian/releases/download/v${version}/neosian-installer.sh"
    curl --proto '=https' --tlsv1.2 -fsSL "$installer" -o "$scratch/install.sh" || fail "could not download $installer"
    # Keep cargo-dist's platform selection, checksums and update receipt.
    # The binary has its own directory; never overwrite a Python tool shim.
    bin_dir="${NEOSIAN_INSTALL_DIR:-$HOME/.neosian/bin}"
    mkdir -p "$bin_dir"
    bin_dir="$(cd "$bin_dir" && pwd -P)"
    NEOSIAN_INSTALL_DIR="$bin_dir" NEOSIAN_NO_MODIFY_PATH=1 sh "$scratch/install.sh"
    expected="$(printf '%s' "$version" | sed 's/rc/-rc./')"
    actual="$("$bin_dir/neosian" version | sed -n '1s/^neosian v//p')"
    [ "$actual" = "$expected" ] || fail "installed version $actual does not match $expected"
    mkdir -p "$HOME/.local/bin"
    link="$HOME/.local/bin/neosian"
    if [ ! -e "$link" ] && [ ! -L "$link" ]; then
        ln -s "$bin_dir/neosian" "$link"
    fi
    selected="$(command -v neosian || true)"
    if [ -z "$selected" ]; then
        echo "  Add $HOME/.local/bin to PATH, or run $bin_dir/neosian."
    elif [ "$selected" != "$bin_dir/neosian" ] && [ "$(readlink "$selected" || true)" != "$bin_dir/neosian" ]; then
        echo "  PATH currently selects $selected. This binary is $bin_dir/neosian."
        echo "  Run '$bin_dir/neosian status' to inspect competing installations."
    fi
    ok "installed neosian v$actual at $bin_dir/neosian"
    printf '\nNext:\n  "%s/neosian" setup --write\n  "%s/neosian" status\n' "$bin_dir" "$bin_dir"
    exit 0
fi

# --- 1. uv ------------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
    ok "uv present: $(uv --version)"
else
    command -v curl >/dev/null 2>&1 || fail "curl is required to fetch uv ${UV_VERSION}"
    echo "  uv not found — installing uv ${UV_VERSION} from ${UV_INSTALLER}"
    curl -LsSf "$UV_INSTALLER" -o "$scratch/uv.sh"
    sh "$scratch/uv.sh"
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
bin_dir="$(uv tool dir --bin)"
binary_dir="${NEOSIAN_INSTALL_DIR:-$HOME/.neosian/bin}"
set --
if [ -d "$binary_dir" ]; then
    binary_dir="$(cd "$binary_dir" && pwd -P)"
    if [ -L "$bin_dir/neosian" ] && [ "$(readlink "$bin_dir/neosian")" = "$binary_dir/neosian" ]; then
        # An explicit Python install may replace the link this script made.
        # uv owns the new shim; the binary and its receipt stay in place.
        set -- --force
        echo "  Selecting the Python shell at $bin_dir/neosian; the binary remains at $binary_dir/neosian."
    fi
fi
if [ -n "$find_links" ]; then
    [ -d "$find_links" ] || fail "--find-links: not a directory: $find_links"
    uv tool install --python ">=3.12" "$@" --find-links "$find_links" "$spec"
    ok "installed $spec from $find_links"
else
    uv tool install --python ">=3.12" "$@" "$spec"
    ok "installed $spec"
fi

# --- 3. PATH ----------------------------------------------------------------
selected="$(command -v neosian || true)"
if [ -n "$selected" ]; then
    if [ "$selected" != "$bin_dir/neosian" ]; then
        echo "  PATH currently selects $selected. The Python shell is $bin_dir/neosian."
    fi
else
    echo "  neosian is installed at ${bin_dir} but that directory is not on PATH."
    echo "  Run 'uv tool update-shell', or add this line to your shell rc:"
    echo "      export PATH=\"${bin_dir}:\$PATH\""
    export PATH="${bin_dir}:$PATH"
fi
banner="$("$bin_dir/neosian" version)"
actual="$(printf '%s\n' "$banner" | grep -o 'v[0-9][0-9A-Za-z.]*' | head -1)"
ok "Python shell installed: $bin_dir/neosian $actual"

# --- 4. the next step -------------------------------------------------------
cat <<NEXT

Next:
  "$bin_dir/neosian" setup --write    # wire every agent client found: MCP + the ledger's hooks
  "$bin_dir/neosian" status           # is this machine set up (home, keys by name, each client)
  "$bin_dir/neosian"                  # talk to your memory; agents: neosian docs cli --json
NEXT
