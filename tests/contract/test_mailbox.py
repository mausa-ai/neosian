"""All ten mailbox operations through the CLI, including occurrence fences."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from tests.contract.schema import validate
from tests.contract.support import Binary


def test_mailbox_lifecycle(binary: Binary) -> None:
    def command(action: str, *args: str) -> dict[str, Any]:
        envelope = binary.json(
            "messages",
            action,
            "--scope",
            "user:contract",
            "--session",
            "worker",
            "--actor",
            "cli:contract",
            *args,
        )
        assert envelope["success"] is True
        result: dict[str, Any] = json.loads(envelope["data"])
        if "id" in result:
            validate(
                "Mailbox",
                {k: v for k, v in result.items() if k not in {"version", "history"}},
            )
        return result

    sent = command("send", "--body", "Review the decision", "--actionable")
    assert str(UUID(sent["id"])) == sent["id"]
    assert (sent["version"], sent["occurrence"], sent["status"], sent["actor"]) == (
        1,
        1,
        "open",
        "cli:contract",
    )
    identity = ("--message-id", sent["id"])
    occurrence = (*identity, "--occurrence", "1")
    assert command("list")["items"] == [sent]
    viewed = command("view", *identity)
    assert viewed["body"] == "Review the decision"
    assert len(viewed["history"]) == 1
    acked = command("ack", *occurrence)
    assert acked["read_by"] == "worker" and acked["status"] == "open"
    claimed = command("claim", *occurrence)
    token = claimed["claim_token"]
    assert str(UUID(token)) == token
    assert claimed["owner"] == "worker"
    fenced = (*occurrence, "--token", token)
    renewed = command("renew", *fenced)
    assert renewed["claim_token"] == token
    assert renewed["claim_until"] >= claimed["claim_until"]
    released = command("release", *fenced)
    assert released["owner"] is None and released["claim_token"] is None
    claimed_again = command("claim", *occurrence)
    assert claimed_again["claim_token"] != token
    wrong = binary.run(
        "messages",
        "complete",
        "--scope",
        "user:contract",
        "--session",
        "worker",
        *fenced,
        "--outcome",
        "done",
        "--json",
    )
    assert wrong.returncode == 1
    assert json.loads(wrong.stdout) == {
        "success": False,
        "error": "a current claim token owned by this session is required",
    }
    current = (*occurrence, "--token", claimed_again["claim_token"])
    snoozed = command(
        "snooze", *current, "--delay-seconds", "3600", "--outcome", "Waiting"
    )
    assert snoozed["occurrence"] == 2
    assert snoozed["claim_token"] is None and snoozed["read_at"] is None
    stale = binary.run(
        "messages",
        "cancel",
        "--scope",
        "user:contract",
        "--session",
        "worker",
        *occurrence,
        "--json",
    )
    assert stale.returncode == 1
    assert json.loads(stale.stdout) == {
        "success": False,
        "error": "stale occurrence; view the message again",
    }
    cancelled = command("cancel", *identity, "--occurrence", "2")
    assert cancelled["status"] == "cancelled"
    assert command("cancel", *identity, "--occurrence", "2") == cancelled

    another = command("send", "--body", "Finish the review", "--actionable")
    second = ("--message-id", another["id"], "--occurrence", "1")
    claim = command("claim", *second)
    completion = (*second, "--token", claim["claim_token"], "--outcome", "Reviewed")
    completed = command("complete", *completion)
    assert (completed["status"], completed["outcome"]) == ("done", "Reviewed")
    assert command("complete", *completion) == completed
