"""The public installer in an isolated home, without a network or real uv."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import neosian

_ROOT = Path(__file__).resolve().parents[2]
_INSTALLER = _ROOT / "scripts/install.sh"


def executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def install_env(tmp_path: Path) -> dict[str, str]:
    if os.name == "nt":
        pytest.skip("the shell installer targets macOS and Linux")
    home = tmp_path / "home with spaces"
    home.mkdir()
    commands = tmp_path / "commands"
    commands.mkdir()
    # A controlled PATH also exercises macOS's shasum fallback on Linux.
    for name in (
        "sh",
        "cat",
        "chmod",
        "mkdir",
        "mktemp",
        "rm",
        "sed",
        "grep",
        "head",
        "ln",
        "readlink",
        "shasum",
    ):
        command = shutil.which(name)
        assert command is not None, name
        (commands / name).symlink_to(command)
    executable(
        commands / "uname",
        '#!/bin/sh\ncase "$1" in -s) echo Darwin;; *) echo arm64;; esac\n',
    )
    executable(commands / "uv", "#!/bin/sh\necho unexpected uv >&2\nexit 99\n")
    executable(
        commands / "curl",
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "url = next(a for a in args if a.startswith('https://'))\n"
        "pathlib.Path(os.environ['CALLS']).write_text(url)\n"
        "if os.environ.get('DOWNLOAD_FAIL'): sys.exit(22)\n"
        "assert url.endswith('/neosian-installer.sh'), url\n"
        "out = pathlib.Path(args[args.index('-o') + 1])\n"
        "out.write_text(pathlib.Path(os.environ['DIST_INSTALLER']).read_text())\n",
    )
    dist = tmp_path / "dist-installer.sh"
    dist.write_text(
        "#!/bin/sh\nset -eu\n" 'test "$NEOSIAN_NO_MODIFY_PATH" = 1\n'
        # The same invocation as cargo-dist: ensure the fallback calculates
        # a real SHA-256, including its -b flag, instead of skipping validation.
        'printf abc > "$HOME/input"\n'
        'sha256sum -b "$HOME/input" > "$HOME/digest"\n'
        'test "${CHECKSUM_FAIL:-0}" = 0 || exit 1\n'
        'mkdir -p "$NEOSIAN_INSTALL_DIR"\n'
        'cat > "$NEOSIAN_INSTALL_DIR/neosian" <<BIN\n'
        "#!/bin/sh\n"
        f'echo "neosian v${{INSTALLED_VERSION:-{neosian.__version__}}}"\n'
        'BIN\nchmod +x "$NEOSIAN_INSTALL_DIR/neosian"\n',
        encoding="utf-8",
    )
    return {
        "HOME": str(home),
        "PATH": f"{home}/.local/bin:{commands}",
        "DIST_INSTALLER": str(dist),
        "CALLS": str(tmp_path / "calls"),
        "TMPDIR": str(tmp_path),
    }


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(_INSTALLER), *args],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.unit
def test_binary_install_works_without_uv_and_checks_with_shasum(
    install_env: dict[str, str],
) -> None:
    result = run(install_env)
    assert result.returncode == 0, result.stderr
    home = Path(install_env["HOME"])
    binary = home / ".neosian/bin/neosian"
    assert (home / ".local/bin/neosian").resolve() == binary
    assert (
        (home / "digest")
        .read_text()
        .startswith("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    )
    assert f"/v{neosian.__version__}/" in Path(install_env["CALLS"]).read_text()
    assert "setup --write" in result.stdout
    # Repeat installation keeps the one PATH link and succeeds.
    assert run(install_env).returncode == 0


@pytest.mark.unit
def test_a_python_shim_is_preserved_and_named(install_env: dict[str, str]) -> None:
    home = Path(install_env["HOME"])
    shim = home / ".local/bin/neosian"
    shim.parent.mkdir(parents=True)
    executable(shim, "#!/bin/sh\necho python shim\n")
    result = run(install_env)
    assert result.returncode == 0, result.stderr
    assert shim.read_text() == "#!/bin/sh\necho python shim\n"
    assert f"PATH currently selects {shim}" in result.stdout


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["DOWNLOAD_FAIL", "CHECKSUM_FAIL"])
def test_download_or_checksum_failure_stops_install(
    install_env: dict[str, str], failure: str
) -> None:
    result = run({**install_env, failure: "1"})
    assert result.returncode != 0
    assert not (Path(install_env["HOME"]) / ".neosian/bin/neosian").exists()


@pytest.mark.unit
def test_a_wrong_installed_version_is_not_reported_as_success(
    install_env: dict[str, str],
) -> None:
    result = run({**install_env, "INSTALLED_VERSION": "0.0.1"})
    assert result.returncode == 1
    assert "does not match" in result.stderr
    assert not (Path(install_env["HOME"]) / ".local/bin/neosian").exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    "args", [("--version",), ("--find-links",), ("--version", "../bad"), ("--unknown",)]
)
def test_bad_flags_do_not_download(
    install_env: dict[str, str], args: tuple[str, ...]
) -> None:
    assert run(install_env, *args).returncode == 2
    assert not Path(install_env["CALLS"]).exists()


@pytest.mark.unit
@pytest.mark.parametrize("existing", [None, "binary", "unrelated"])
def test_python_route_keeps_the_uv_install(
    install_env: dict[str, str], existing: str | None
) -> None:
    home = Path(install_env["HOME"])
    if existing:
        assert run(install_env).returncode == 0
        Path(install_env["CALLS"]).unlink()
        if existing == "unrelated":
            shim = home / ".local/bin/neosian"
            shim.unlink()
            shim.symlink_to(home / "someone-elses-command")
    binary = home / ".neosian/bin/neosian"
    before = binary.read_bytes() if binary.exists() else None
    commands = Path(install_env["PATH"].split(":")[-1])
    executable(
        commands / "uv",
        "#!/bin/sh\nset -eu\n"
        'test "$1" != --version || { echo "uv test"; exit 0; }\n'
        'test "$2" != dir || { echo "$HOME/.local/bin"; exit 0; }\n'
        'printf "%s\\n" "$@" > "$HOME/uv-args"\n'
        'mkdir -p "$HOME/.local/bin"\n'
        'if [ -L "$HOME/.local/bin/neosian" ]; then\n'
        '  case " $* " in\n'
        '    *" --force "*) rm "$HOME/.local/bin/neosian";;\n'
        "    *) exit 2;;\n"
        "  esac\n"
        "fi\n"
        'cat > "$HOME/.local/bin/neosian" <<BIN\n'
        "#!/bin/sh\n"
        f'echo "neosian v{neosian.__version__}"\n'
        'BIN\nchmod +x "$HOME/.local/bin/neosian"\n',
    )
    result = run(install_env, "--python")
    assert result.returncode == (2 if existing == "unrelated" else 0), result.stderr
    assert (Path(install_env["HOME"]) / "uv-args").read_text().splitlines() == [
        "tool",
        "install",
        "--python",
        ">=3.12",
        *(["--force"] if existing == "binary" else []),
        f"neosian=={neosian.__version__}",
    ]
    if before is not None:
        assert binary.read_bytes() == before
    assert not Path(install_env["CALLS"]).exists()
