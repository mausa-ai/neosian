"""An install through the `auto` policy at interactive `status` hands over to the
installed release's refresh before status runs (DESIGN §40.48)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.contract.refresh_support import Machine, machine, outdated_adapter
from tests.contract.support import Binary, package_version

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX terminal")


def terminal(argv: list[str], env: dict[str, str], cwd: Path) -> tuple[int, str, str]:
    """Run with stdout on a pseudo-terminal, so status is the human door."""
    import pty

    primary, secondary = pty.openpty()
    chunks: list[bytes] = []

    def drain() -> None:
        while True:
            try:
                chunk = os.read(primary, 4096)
            except OSError:
                return
            if not chunk:
                return
            chunks.append(chunk)

    reader = threading.Thread(target=drain)
    reader.start()
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=secondary,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            timeout=60,
            check=False,
        )
    finally:
        os.close(secondary)
    reader.join(timeout=10)
    os.close(primary)
    text = b"".join(chunks).decode().replace("\r\n", "\n")
    return result.returncode, text, result.stderr.decode()


def installation(host: Machine, newer: str) -> tuple[Path, dict[str, str]]:
    """This build as the running release, and an updater that installs a newer
    one: it reports `newer` to `version` and runs this build for everything else."""
    binary = host.binary
    prefix = host.root / "install dir"
    prefix.mkdir()
    running = prefix / "neosian"
    shutil.copy2(binary.executable, running)
    log = host.root / "installed-release.log"
    (prefix / "next").write_text(
        "#!/bin/sh\n"
        f"[ \"$1\" = version ] && echo 'neosian v{newer}' && exit\n"
        f"echo \"$*\" >> '{log}'\n"
        f"exec '{binary.executable}' \"$@\"\n"
    )
    stamp = binary.home / "binary-update-check"
    updater = prefix / "neosian-update"
    updater.write_text(
        "#!/bin/sh\n"
        f"[ \"$1 $2\" = '--tag v{newer}' ] || exit 8\n"
        'rm -f "$(dirname "$0")/neosian"\n'
        'cp "$(dirname "$0")/next" "$(dirname "$0")/neosian"\n'
        'chmod 700 "$(dirname "$0")/neosian"\n'
        # The installed release is current: the re-run status finds nothing new.
        f"echo \"$(date -u +%Y-%m-%dT%H:%M:%SZ) {package_version()}\" > '{stamp}'\n"
        "echo installed\n"
    )
    updater.chmod(0o700)
    receipts = host.root / "receipt"
    receipts.mkdir()
    (receipts / "neosian-receipt.json").write_text(
        json.dumps(
            {
                "install_prefix": str(prefix),
                "binaries": ["neosian"],
                "version": package_version(),
                "provider": {"source": "cargo-dist", "version": "0.32.0"},
                "source": {
                    "release_type": "github",
                    "owner": "mausa-ai",
                    "name": "neosian",
                    "app_name": "neosian",
                },
            }
        )
    )
    binary.home.mkdir(exist_ok=True)
    (binary.home / "config.toml").write_text('[update]\nmode = "auto"\n')
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp.write_text(f"{now} {newer}\n")
    # The stand-in scripts need the system tools; no client CLI is involved.
    path = f"{host.root / 'commands'}{os.pathsep}/usr/bin{os.pathsep}/bin"
    return running, {"AXOUPDATER_CONFIG_PATH": str(receipts), "PATH": path}


def test_auto_policy_at_status_refreshes_before_status_runs(binary: Binary) -> None:
    host = machine(binary)
    result = host.run("setup", "--client=opencode", "--client=pi", "--write")
    assert result.returncode == 0, result.stdout + result.stderr
    plugin = host.home("opencode") / "plugins/neosian-record.js"
    outdated_adapter(plugin)
    base, patch = package_version().rsplit(".", 1)
    newer = f"{base}.{int(patch) + 1}"
    running, extra = installation(host, newer)

    code, status, notes = terminal(
        [str(running), "status"], host.environment(**extra), host.project
    )
    assert code == 0, status + notes
    log = (host.root / "installed-release.log").read_text()
    assert log == "setup --refresh\nstatus\n"
    assert "installed\n" in notes
    assert "OpenCode: updated" in notes and "Pi: current" in notes
    assert notes.rstrip().endswith(
        f"Restart OpenCode and Pi to load neosian {package_version()}."
    )
    assert "refreshes the user-level registrations" not in status
    assert "opencode" in status and "update    mode auto" in status
    assert "__NEOSIAN_RECORD_ARGV__" not in plugin.read_text()
    assert "an earlier neosian adapter" not in plugin.read_text()
    check = host.run("setup", "--check", "--client=opencode", "--client=pi")
    assert check.returncode == 0, check.stdout
