"""An install through the `auto` policy at interactive `status` hands over to the
installed release's refresh before status runs (DESIGN §40.48), and a new major
is refused, installed by hand and then refreshed (the 2.20.0 to 3.0.0 path)."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.contract.refresh_support import (
    CLIENTS,
    FORMER,
    RECEIPT,
    SERVER,
    Machine,
    by_client,
    machine,
    migrations,
    outdated_adapter,
    renamed,
)
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


def installation(
    host: Machine, newer: str, reject: bool = False
) -> tuple[Path, dict[str, str]]:
    """This build as the running release, and an updater that installs a newer
    one: it reports `newer` to `version` and runs this build for everything else
    (or, with `reject`, refuses the refresh as a release without it would)."""
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
        + (
            "[ \"$1\" = setup ] && echo 'unexpected argument' >&2 && exit 2\n"
            if reject
            else ""
        )
        + f"exec '{binary.executable}' \"$@\"\n"
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
        "Restart OpenCode to load these changes.\n"
        f"neosian {newer} is installed; restart running clients so they use it."
    )
    assert notes.index("Refreshing") < notes.index("OpenCode: updated")
    assert "refreshes the user-level registrations" not in status
    assert "opencode" in status and "update    mode auto" in status
    assert "__NEOSIAN_RECORD_ARGV__" not in plugin.read_text()
    assert "an earlier neosian adapter" not in plugin.read_text()
    check = host.run("setup", "--check", "--client=opencode", "--client=pi")
    assert check.returncode == 0, check.stdout


@pytest.mark.parametrize("outcome", ["failed", "incomplete"])
def test_status_still_runs_after_a_refresh_that_did_not_finish(
    binary: Binary, outcome: str
) -> None:
    host = machine(binary)
    result = host.run("setup", "--client=claude-code", "--client=pi", "--write")
    assert result.returncode == 0, result.stdout + result.stderr
    claude = host.home("claude-code")
    (claude / "neosian-cache/plugin/hooks/display.js").write_text("// earlier\n")
    base, patch = package_version().rsplit(".", 1)
    newer = f"{base}.{int(patch) + 1}"
    running, extra = installation(host, newer, reject=outcome == "incomplete")

    code, status, notes = terminal(
        [str(running), "status"],
        host.environment(**extra, CLAUDE_FAIL="plugin install"),
        host.project,
    )
    assert code == 0, status + notes
    assert "update    mode auto" in status
    log = (host.root / "installed-release.log").read_text()
    assert log == "setup --refresh\nstatus\n"
    if outcome == "failed":
        assert "Claude Code: failed; nothing was changed" in notes
        assert "  hint    fix the cause, then run neosian setup --refresh" in notes
    else:
        assert "did not complete the registration refresh (exit 2)" in notes
        assert "hint: run neosian setup --refresh" in notes
    assert (claude / "neosian-cache/plugin/hooks/display.js").read_text() == (
        "// earlier\n"
    )


# Where each client keeps neosian's MCP entry (Codex: its `config.toml` table).
REGISTRATIONS = {
    "claude-code": (".claude.json", "mcpServers"),
    "opencode": ("opencode.json", "mcp"),
    "muse-code": ("settings.json", "mcpServers"),
    "cursor": ("mcp.json", "mcpServers"),
    "pi": ("mcp.json", "mcpServers"),
}


def as_2_20_0(host: Machine) -> dict[str, Any]:
    """Every client as 2.20.0 left it: neosian's entry under `neosian-memory`
    and no receipt. Answers each client's entry."""
    entries = {}
    for client, (name, key) in REGISTRATIONS.items():
        path = host.home(client) / name
        document = json.loads(path.read_text())
        entries[client] = document[key][SERVER]
        document[key] = renamed(document[key], SERVER, FORMER)
        path.write_text(json.dumps(document, indent=1))
    config = host.home("codex") / "config.toml"
    config.write_text(
        config.read_text().replace(f"[mcp_servers.{SERVER}]", f"[mcp_servers.{FORMER}]")
    )
    for client in CLIENTS:
        (host.home(client) / RECEIPT).unlink(missing_ok=True)
    return entries


def names(host: Machine) -> dict[str, list[str]]:
    """The names neosian's entry stands under, per client."""
    found = {}
    for client, (name, key) in REGISTRATIONS.items():
        members = json.loads((host.home(client) / name).read_text())[key]
        found[client] = [k for k in members if k in (FORMER, SERVER)]
    text = (host.home("codex") / "config.toml").read_text()
    found["codex"] = [k for k in (FORMER, SERVER) if f"[mcp_servers.{k}]" in text]
    return found


def test_a_new_major_is_refused_then_installed_by_hand_and_refreshed(
    binary: Binary,
) -> None:
    """The documented path from 2.20.0 to 3.0.0 (`neosian docs cli`, the
    changelog's "Upgrading from 2.20.0"). `crates/neosian/src/update/` is
    unchanged since `v2.20.0` and this tree still says 2.20.0, so the refusal
    here is the released one of 3.0.0 (after the version bump, the same rule one
    major later). The updater refuses the new major with the command to run and
    installs and refreshes nothing; the person runs that command, then `setup
    --refresh`, which moves every client to `neosian`; a second run changes
    nothing, and `setup --undo-rename` puts them back. The installed release and
    its updater are stand-ins (`installation`); a real 2.20.0 binary and a
    published 3.0.0 archive are the owner's run."""
    host = machine(binary)
    host.setup()
    entries = as_2_20_0(host)
    newer = f"{int(package_version().split('.')[0]) + 1}.0.0"
    running, extra = installation(host, newer)
    environment = host.environment(**extra)
    before = running.read_bytes()

    code, status, notes = terminal([str(running), "status"], environment, host.project)
    assert code == 0, status + notes
    refusal = f"not applied ({newer} is a new major); run it yourself: "
    [line] = [line for line in notes.splitlines() if refusal in line]
    assert running.read_bytes() == before
    assert not (host.root / "installed-release.log").exists()
    assert "Refreshing" not in notes and "is installed" not in notes
    assert names(host) == {client: [FORMER] for client in CLIENTS}

    command = shlex.split(line.split(refusal, 1)[1])
    assert command == [str(running.parent / "neosian-update"), "--tag", f"v{newer}"]
    installed = subprocess.run(
        command, env=environment, capture_output=True, text=True, timeout=60
    )
    assert installed.returncode == 0, installed.stderr
    assert names(host) == {client: [FORMER] for client in CLIENTS}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(running), *args],
            cwd=host.project,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    assert run("version").stdout == f"neosian v{newer}\n"
    refreshed = run("setup", "--refresh", "--json")
    assert refreshed.returncode == 0, refreshed.stdout + refreshed.stderr
    rows = by_client(json.loads(refreshed.stdout))
    assert {c: rows[c]["rename"]["state"] for c in CLIENTS} == dict.fromkeys(
        CLIENTS, "moved"
    )
    assert names(host) == {client: [SERVER] for client in CLIENTS}
    for client, (name, key) in REGISTRATIONS.items():
        document = json.loads((host.home(client) / name).read_text())
        assert document[key][SERVER] == entries[client], client
    assert (host.root / "installed-release.log").read_text() == (
        "setup --refresh --json\n"
    )
    assert len(migrations(host)) == 1

    again = run("setup", "--refresh", "--json")
    assert again.returncode == 0, again.stdout + again.stderr
    rows = by_client(json.loads(again.stdout))
    assert all(rows[c]["rename"]["state"] == "unchanged" for c in CLIENTS if c in rows)
    assert len(migrations(host)) == 1

    back = run("setup", "--undo-rename")
    assert back.returncode == 0, back.stdout + back.stderr
    assert names(host) == {client: [FORMER] for client in CLIENTS}
