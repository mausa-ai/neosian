"""Exercise the real cargo-dist installer against locally built artifacts.

Run `dist build --artifacts=host --installer=shell` first. Only curl's
transport is replaced; the generated script, archive, updater and public
entry point are real. No installed client or user home is touched.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check() -> None:
    artifacts = ROOT / "target/distrib"
    version_match = re.search(
        r'^APP_VERSION="([^"]+)"$',
        (artifacts / "neosian-installer.sh").read_text(encoding="utf-8"),
        re.M,
    )
    assert version_match
    expected = version_match.group(1)
    curl = shutil.which("curl")
    assert curl
    with tempfile.TemporaryDirectory(prefix="neosian-install-") as directory:
        work = Path(directory)
        commands = work / "commands"
        commands.mkdir()
        transport = commands / "curl"
        transport.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, shutil, subprocess, sys\n"
            "args = sys.argv[1:]\n"
            "if '--help' in args or '--version' in args:\n"
            "    sys.exit(subprocess.call([os.environ['REAL_CURL'], *args]))\n"
            "url = next(a for a in args if a.startswith('https://'))\n"
            "assert url.startswith('https://github.com/mausa-ai/neosian/releases/download/')\n"
            "name = url.rsplit('/', 1)[1]\n"
            "flag = '-o' if '-o' in args else '--output'\n"
            "target = pathlib.Path(args[args.index(flag) + 1])\n"
            "shutil.copyfile(pathlib.Path(os.environ['ARTIFACTS']) / name, target)\n"
            "if os.environ.get('CORRUPT') and name.endswith('.tar.xz'):\n"
            "    with target.open('ab') as f: f.write(b'corrupt')\n",
            encoding="utf-8",
        )
        transport.chmod(0o755)
        # System tools only, no tool-manager paths where uv could live.
        env = {
            "PATH": f"{commands}:/usr/bin:/bin:/usr/sbin:/sbin",
            "REAL_CURL": curl,
            "ARTIFACTS": str(artifacts),
            "TMPDIR": str(work),
        }
        for corrupt in (False, True):
            home = work / ("corrupt" if corrupt else "home with spaces")
            home.mkdir()
            run_env = {
                **env,
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }
            if corrupt:
                run_env["CORRUPT"] = "1"
            assert shutil.which("uv", path=run_env["PATH"]) is None
            result = subprocess.run(
                ["sh", str(ROOT / "scripts/install.sh")],
                env=run_env,
                capture_output=True,
                text=True,
                timeout=90,
            )
            binary = home / ".neosian/bin/neosian"
            if corrupt:
                assert result.returncode != 0, result.stdout
                assert "checksum mismatch" in result.stderr, result.stderr
                assert not binary.exists()
                continue
            assert result.returncode == 0, result.stdout + result.stderr
            receipt = json.loads(
                (home / ".config/neosian/neosian-receipt.json").read_text()
            )
            assert receipt["version"] == expected
            assert receipt["install_prefix"] == str(binary.parent.resolve())
            assert receipt["source"]["owner"] == "mausa-ai"
            assert receipt["source"]["name"] == "neosian"
            assert (binary.parent / "neosian-update").is_file()
            assert (home / ".local/bin/neosian").resolve() == binary.resolve()
            version = subprocess.check_output(
                [binary, "version"], env=run_env, text=True
            )
            assert version.strip() == f"neosian v{expected}"
            assert f'"{binary.resolve()}" setup --check' in result.stdout
            # A new home has no clients yet; the isolated product probe still passes.
            checked = subprocess.run(
                [binary, "setup", "--check", "--json"],
                env=run_env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert checked.returncode == 1, checked.stdout + checked.stderr
            report = json.loads(checked.stdout)
            assert report["probe"]["success"] is True
            assert report["client_connection"] == "not_observed"
            docs = subprocess.check_output(
                [binary, "docs", "quickstart"], env=run_env, text=True
            )
            source = (ROOT / "assets/docs/quickstart.md").read_text(encoding="utf-8")
            assert docs == source.split("---", 2)[2].strip() + "\n"
            print(
                f"installed {expected}; receipt, updater, PATH and embedded docs verified"
            )
        print("corrupt archive refused before installation")


if __name__ == "__main__":
    check()
