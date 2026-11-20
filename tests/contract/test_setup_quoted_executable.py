"""An installation whose path needs quoting is still recognized as ours."""

from __future__ import annotations

import json
import os
import shutil

import pytest

from tests.contract.refresh_support import by_client, machine
from tests.contract.support import Binary

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX client CLI doubles")
CLIENTS = ("claude-code", "codex", "cursor")
FILES = {"claude-code": "settings.json", "codex": "hooks.json", "cursor": "hooks.json"}


def test_hooks_of_an_installation_in_a_spaced_path_are_ours(binary: Binary) -> None:
    spaced = binary.directory / "my bin" / "neosian"
    spaced.parent.mkdir()
    shutil.copy2(binary.executable, spaced)
    host = machine(Binary(spaced, binary.directory))

    host.setup()
    for client in CLIENTS:
        again = host.run("record", "install", "--client", client, "--write")
        assert again.returncode == 0, again.stdout + again.stderr

    for client in CLIENTS:
        document = json.loads((host.home(client) / FILES[client]).read_text())
        for event, groups in document["hooks"].items():
            ours = [
                hook.get("command", "")
                for group in groups
                for hook in [group, *group.get("hooks", [])]
                if " record " in hook.get("command", "")
            ]
            assert len(ours) == 1, (client, event, ours)
            assert ours[0].startswith(f"'{spaced}' record "), ours[0]

    status = json.loads(host.run("status", "--json").stdout)
    present = {
        entry["client"]: entry["hooks_present"]
        for entry in status["clients"]
        if entry["client"] in CLIENTS
    }
    assert present == dict.fromkeys(CLIENTS, True), present

    report = host.refresh()
    for client in CLIENTS:
        kinds = {part["kind"] for part in by_client(report)[client]["parts"]}
        assert "hooks" in kinds, (client, kinds)
