"""The mailbox shell derives the same default layout as memory commands."""

import io
import json
from pathlib import Path

import pytest

from neosian import FileStore, MemoryConfig, project_mounts
from neosian._foundation.messaging.cli import run
from neosian.messaging import Mailbox, MessageTarget


@pytest.mark.parametrize("json_output", [False, True])
async def test_cli_uses_home_and_current_project_mounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, json_output: bool
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    root = tmp_path / "home"
    mounts = project_mounts(project)
    mailbox = Mailbox(MemoryConfig(FileStore(root), mounts))
    for mount in mounts:
        await mailbox.send(MessageTarget(mount.scope), f"For {mount.mount_path}")

    flags = ["--json"] if json_output else []
    out, err = io.StringIO(), io.StringIO()
    code = await run(["list", *flags], {"NEOSIAN_HOME": str(root)}, out=out, err=err)
    assert code == 0, err.getvalue()
    assert err.getvalue() == ""
    payload = json.loads(out.getvalue())
    if json_output:
        assert payload["success"]
        payload = json.loads(payload["data"])
    assert {(item["scope"], item["body"]) for item in payload["items"]} == {
        (mount.scope, f"For {mount.mount_path}") for mount in mounts
    }

    out, err = io.StringIO(), io.StringIO()
    code = await run(
        ["send", "--body", "Default destination", *flags],
        {"NEOSIAN_HOME": str(root)},
        out=out,
        err=err,
    )
    assert code == 0, err.getvalue()
    messages = await mailbox.all()
    sent = next(r.message for r in messages if r.message.body == "Default destination")
    assert sent.scope == next(m.scope for m in mounts if m.mount_path == "project")
