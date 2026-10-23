"""Verify published binaries, the MSI and fresh registry installations.

Run from the release's checkout. --artifacts rehearses archive/MSI checks
against cargo-dist's local output; normal runs download the real release.
The MSI mode is intended for an ephemeral Windows runner: it installs and
uninstalls the package. Other modes keep installations in a temporary directory.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
TARGETS = (
    "aarch64-apple-darwin",
    "aarch64-unknown-linux-gnu",
    "x86_64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "x86_64-pc-windows-msvc",
)


def asset(name: str, work: Path, artifacts: Path | None) -> Path:
    path = work / name
    if artifacts is not None:
        shutil.copyfile(artifacts / name, path)
    else:
        url = f"https://github.com/mausa-ai/neosian/releases/download/v{VERSION}/{name}"
        request = Request(url, headers={"User-Agent": "neosian-release-verification/1"})
        with urlopen(request, timeout=90) as response:
            path.write_bytes(response.read())
    return path


def checked(name: str, work: Path, artifacts: Path | None) -> Path:
    path = asset(name, work, artifacts)
    expected = asset(f"{name}.sha256", work, artifacts).read_text().split()[0]
    assert re.fullmatch(r"[0-9a-f]{64}", expected), "malformed SHA-256"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
    return path


def output(binary: Path, *args: str, env: dict[str, str], work: Path) -> str:
    return subprocess.check_output(
        [str(binary), *args], env=env, cwd=work, text=True, timeout=60
    )


def verify(
    binary: Path, work: Path, env: dict[str, str], *, python: bool = False
) -> None:
    banner = output(binary, "version", env=env, work=work)
    if python:
        assert f"v{VERSION}" in banner, banner
    else:
        assert banner.strip() == f"neosian v{VERSION}", banner
    docs = output(binary, "docs", "quickstart", env=env, work=work)
    page = (ROOT / "neosian/assets/docs/quickstart.md").read_text(encoding="utf-8")
    assert docs == page.split("---", 2)[2].strip() + "\n", "embedded docs differ"
    status = json.loads(output(binary, "status", "--json", env=env, work=work))
    assert status["version"] == VERSION, status
    print(f"verified {binary}: version {VERSION}, quickstart and status", flush=True)


def archive(
    work: Path, env: dict[str, str], target: str, artifacts: Path | None
) -> None:
    windows = target.endswith("windows-msvc")
    suffix = ".zip" if windows else ".tar.xz"
    path = checked(f"neosian-{target}{suffix}", work, artifacts)
    name = "neosian.exe" if windows else "neosian"
    # Read only the executable, never extract paths supplied by an archive.
    if windows:
        with zipfile.ZipFile(path) as package:
            members = [p for p in package.namelist() if PurePosixPath(p).name == name]
            assert len(members) == 1, members
            data = package.read(members[0])
    else:
        with tarfile.open(path) as package:
            files = [
                p for p in package.getmembers() if PurePosixPath(p.name).name == name
            ]
            assert len(files) == 1 and files[0].isfile(), files
            stream = package.extractfile(files[0])
            assert stream is not None
            data = stream.read()
    binary = work / name
    binary.write_bytes(data)
    binary.chmod(0o755)
    verify(binary, work, env)


def msi(work: Path, env: dict[str, str], artifacts: Path | None) -> None:
    assert os.name == "nt", "MSI verification requires Windows"
    path = checked("neosian-x86_64-pc-windows-msvc.msi", work, artifacts)
    destination = work / "msi installation"

    def install(action: str, *properties: str) -> None:
        log = work / f"msi-{action}.log"
        result = subprocess.run(
            [
                "msiexec.exe",
                f"/{action}",
                str(path),
                "/qn",
                "/norestart",
                "/L*v",
                str(log),
                *properties,
            ],
            env=env,
            timeout=180,
            check=False,
        )
        if result.returncode not in (0, 3010):
            print(log.read_text(encoding="utf-16", errors="replace")[-12000:])
            raise RuntimeError(f"msiexec /{action} failed: {result.returncode}")

    install("i", f"APPLICATIONFOLDER={destination}")
    try:
        verify(destination / "bin/neosian.exe", work, env)
    finally:
        install("x")
    assert not (destination / "bin/neosian.exe").exists(), "MSI did not uninstall"


def channels(work: Path, env: dict[str, str]) -> None:
    assert os.name != "nt", "the registry rehearsal runs on macOS or Linux"
    npm_prefix, cargo_prefix, python_bin = (
        work / "npm",
        work / "cargo",
        work / "python-bin",
    )
    env.update(
        {
            "UV_TOOL_DIR": str(work / "python-tools"),
            "UV_TOOL_BIN_DIR": str(python_bin),
            "UV_CACHE_DIR": str(work / "uv-cache"),
            "CARGO_TARGET_DIR": str(work / "cargo-target"),
        }
    )
    for key in ("NPM_CONFIG_USERCONFIG", "NPM_CONFIG_GLOBALCONFIG"):
        path = work / key
        path.write_text("")
        env[key] = str(path)
    subprocess.run(
        [
            "npm",
            "install",
            "--global",
            "--prefix",
            str(npm_prefix),
            "--cache",
            str(work / "npm-cache"),
            "--no-audit",
            "--no-fund",
            "--registry",
            "https://registry.npmjs.org",
            f"neosian@{VERSION}",
        ],
        env=env,
        cwd=work,
        check=True,
        timeout=240,
    )
    native = npm_prefix / "bin/neosian"
    verify(native, work, env)
    # Run from ROOT so rustup uses the crate's exact toolchain pin.
    subprocess.run(
        [
            "cargo",
            "install",
            "neosian",
            "--version",
            VERSION,
            "--locked",
            "--root",
            str(cargo_prefix),
            "--registry",
            "crates-io",
        ],
        env=env,
        cwd=ROOT,
        check=True,
        timeout=900,
    )
    verify(cargo_prefix / "bin/neosian", work, env)
    subprocess.run(
        [
            "uv",
            "tool",
            "install",
            "--python",
            "3.12",
            "--default-index",
            "https://pypi.org/simple",
            f"neosian=={VERSION}",
        ],
        env=env,
        cwd=work,
        check=True,
        timeout=240,
    )
    python = python_bin / "neosian"
    env["PATH"] = os.pathsep.join((str(native.parent), str(python_bin), env["PATH"]))
    verify(python, work, env, python=True)
    for binary in (native, python):
        status = json.loads(output(binary, "status", "--json", env=env, work=work))
        assert len(status["path_executables"]) >= 2 and status["shadowing"], status
    print("npm, Cargo and PyPI installations agree; both doors report PATH shadowing")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("archive", "msi", "channels"))
    parser.add_argument(
        "--target", choices=TARGETS, default=os.getenv("NEOSIAN_VERIFY_TARGET")
    )
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), "verify a stable release"
    assert os.getenv("NEOSIAN_VERIFY_VERSION", VERSION) == VERSION, "wrong checkout"
    if args.kind == "archive" and args.target not in TARGETS:
        parser.error("archive needs --target or NEOSIAN_VERIFY_TARGET")
    if args.kind == "channels" and args.artifacts is not None:
        parser.error("channels checks the live registries only")
    with tempfile.TemporaryDirectory(prefix="neosian-release-") as directory:
        work = Path(directory)
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_DSN"))
        }
        env["NEOSIAN_HOME"] = str(work / "state")
        if args.kind == "archive":
            archive(work, env, args.target, args.artifacts)
        elif args.kind == "msi":
            msi(work, env, args.artifacts)
        else:
            channels(work, env)


if __name__ == "__main__":
    main()
