"""Machine and conversation recording controls compose on an assigned profile."""

import json

from tests.contract.profile_support import run
from tests.contract.support import Binary
from tests.contract.test_machine_login import (
    SECRET,
    TRUST,
    headless,
    host_server,
    record,
)


def test_managed_refresh_preserves_the_conversation_resume_boundary(
    binary: Binary,
) -> None:
    credentials = {**TRUST, "NEOSIAN_LOGIN_TOKEN": SECRET}
    with host_server() as host:
        assert headless(binary, host).returncode == 0
        first = record(binary, SECRET, "UserPromptSubmit", prompt="abandoned draft")
        event = json.loads(first.stdout)
        assert event["disposition"] == "spooled"
        conversation = event["session_id"]
        assert (
            binary.json("record", "pause", conversation)["disposition"]
            == "capture_paused"
        )
        spool = binary.home / "profile-spool"
        assert not list(spool.glob("*.jsonl"))

        host.label = "Updated project title"
        host.etag = '"new-title"'
        assert (
            run(
                binary, "login", "--refresh", "--capture", "off", env=credentials
            ).returncode
            == 0
        )
        assert (
            binary.json("record", "resume", conversation)["disposition"]
            == "capture_resumes_on_prompt"
        )
        assert (
            record(
                binary, SECRET, "UserPromptSubmit", prompt="machine is off"
            ).returncode
            == 0
        )
        assert (spool / f"{conversation}.resuming").is_file()
        assert not list(spool.glob("*.jsonl"))
        assert (
            run(binary, "memory", "view", "/project", env=credentials).returncode == 0
        )

        assert (
            run(
                binary, "login", "--refresh", "--capture", "on", env=credentials
            ).returncode
            == 0
        )
        earlier = record(binary, SECRET, "Stop", last_assistant_message="earlier reply")
        assert json.loads(earlier.stdout)["disposition"] == "capture_paused"
        current = record(binary, SECRET, "UserPromptSubmit", prompt="record this turn")
        assert json.loads(current.stdout)["disposition"] == "spooled"
        (pending,) = spool.glob("*.jsonl")
        content = pending.read_bytes()
        assert b"record this turn" in content
        for excluded in (b"abandoned draft", b"machine is off", b"earlier reply"):
            assert excluded not in content
