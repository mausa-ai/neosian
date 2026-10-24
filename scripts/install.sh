#!/bin/sh
# The native binary; Python product installations were retired in v2.
# cargo-dist owns platform selection, archive verification and update receipts.
set -eu

NEOSIAN_RELEASE="2.4.0"

version="${NEOSIAN_VERSION:-$NEOSIAN_RELEASE}"

usage() {
    cat <<USAGE
usage: install.sh [--version X.Y.Z]

  --version X.Y.Z    pin the neosian version (default: ${NEOSIAN_RELEASE},
                     the release this script shipped with)

Environment: NEOSIAN_VERSION mirrors --version.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --python|--find-links) echo "error: the Python product is retired; see neosian docs migration" >&2; exit 2 ;;
        --version)
            if [ $# -lt 2 ] || [ -z "$2" ]; then
                echo "error: $1 needs a value" >&2
                exit 2
            fi
            version="$2"
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

    case "$arch" in
        arm64|aarch64|x86_64|amd64) ;;
        *) fail "unsupported architecture: $arch" ;;
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
