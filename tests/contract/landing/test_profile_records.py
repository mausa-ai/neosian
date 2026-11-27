"""A profile lands as records where its history source keeps them (the
T1e1c slice of the T1e1 plan; its history source is opened alone, as the
profile's routing opens it). The Stop hook queues its turn (a store that
keeps records is never landed inline, ruling E12), and `neosian record-land
--home H` lands each queued turn after resolving the profile from the
turn's state, only while its fingerprint is the turn's."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.contract.landing.conftest import Hold, slots_held
from tests.contract.landing.lander import draft_id, enable, land
from tests.contract.support import Binary
from tests.contract.test_profile_capture import event as profile_event
from tests.contract.test_profile_pause import local

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="flock interoperation")


def event(binary: Binary, name: str, **fields: Any) -> Any:
    """A profile hook event, with the home's landing slots held."""
    with slots_held(binary.home):
        return profile_event(binary, name, **fields)


def rows(history: Path, conversation: str) -> list[Any]:
    log = history / "conversations" / conversation / "turns.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_a_profile_whose_history_keeps_records_lands_its_turns_as_records(
    binary: Binary, hold_lock: Hold
) -> None:
    history = local(binary)
    enable(binary, history)
    conversation = event(binary, "UserPromptSubmit", prompt="profiled")["session_id"]
    queued = event(binary, "Stop", last_assistant_message="answered")
    assert (queued["disposition"], queued["turn"]) == ("pending", None)
    assert land(binary)["landed"] == 1
    [first] = rows(history, conversation)
    assert first["neosian_format"] == 2 and first["actor"] == "codex:native-session"
    spool = binary.home / "profile-spool"
    event(binary, "UserPromptSubmit", prompt="queued")
    with hold_lock(spool / "pending" / conversation):
        queued = event(binary, "Stop", last_assistant_message="later")
    assert queued["disposition"] == "pending"
    [turn] = (spool / "pending" / conversation).glob("*.jsonl")
    line = turn.read_text().split("\n")[0]
    summary = land(binary)
    assert (summary["landed"], summary["left"], summary["faults"]) == (1, [], [])
    second = rows(history, conversation)[1]
    assert (second["neosian_format"], second["turn"]) == (2, 2)
    assert second["draft"] == draft_id(conversation, line)
    assert not list((spool / "pending").glob("*/*.jsonl"))
    document = history / "workspace%3Ahistory" / "documents"
    assert any(document.rglob("*")), "the sessions document is written"
