"""A Conversation lists itself in the project's sessions (§32, #294),
and the recall and search pair registers with it (#299)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from neosian import AgentConfig, Model
from neosian._foundation.conversation.compaction import CompactionConfig
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.llm.base import text_of
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import Mount

PROJECT = "user:demo/proj:app"
MOUNTS = (Mount("user:demo", "user"), Mount(PROJECT, "project"))


def _config(replies: int) -> tuple[AgentConfig, FakeClient]:
    fake = FakeClient(
        FakeScript(turns=tuple(FakeTurn(content=f"reply {i}") for i in range(replies)))
    )
    config = AgentConfig(
        system_prompt="You are a test agent.",
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
    )
    return config, fake


def _tools(fake: FakeClient) -> list[str]:
    return [str(tool.name) for tool in fake.calls[-1].tools]


@pytest.mark.unit
class TestTheSessionsDocument:
    async def test_written_after_each_turn(self, store: FileStore) -> None:
        config, _ = _config(2)
        convo = Conversation(config, store=store, conversation_id="t1", mounts=MOUNTS)
        await convo.send("first question\nsecond line")
        first = await store.read(PROJECT, "sessions/t1")
        assert first is not None
        assert first.content.startswith("# neosian session t1\n")
        assert "- last prompt: first question\n" in first.content
        assert first.content.endswith("- turns: 1\n")
        started = next(
            line for line in first.content.splitlines() if line.startswith("- started")
        )
        await convo.send("second question")
        second = await store.read(PROJECT, "sessions/t1")
        assert second is not None and started in second.content
        assert "- last prompt: second question\n" in second.content
        assert second.content.endswith("- turns: 2\n")
        rows = await store.versions(PROJECT, "sessions/t1")
        assert [(r.version, r.actor) for r in rows] == [
            (2, "conv:t1#2"),
            (1, "conv:t1#1"),
        ]

    async def test_streamed_sends_write_it_before_the_stream_ends(
        self, store: FileStore
    ) -> None:
        config, _ = _config(1)
        convo = Conversation(config, store=store, conversation_id="t1", mounts=MOUNTS)
        seen: list[bool] = []
        async for _ in await convo.send("hi", stream=True):
            seen.append(await store.read(PROJECT, "sessions/t1") is not None)
        assert seen and seen[-1]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"memory_scope": "user:demo"},
            {"mounts": (Mount(PROJECT, "project", read_only=True),)},
            {"mounts": (Mount(PROJECT, "project", edit_only=True),)},
        ],
    )
    async def test_absent_without_a_writable_project_mount(
        self, store: FileStore, kwargs: dict[str, Any]
    ) -> None:
        config, _ = _config(1)
        convo = Conversation(config, store=store, conversation_id="t1", **kwargs)
        await convo.send("hi")
        for scope in ("user:demo", PROJECT):
            assert await store.list_documents(scope, prefix="sessions/") == ()

    async def test_a_failing_store_logs_and_the_send_answers(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        class Boom(FileStore):
            async def write(self, *_args: Any, **_kwargs: Any) -> Any:
                raise OSError("disk full")

        store = Boom(tmp_path / "store")
        config, _ = _config(1)
        convo = Conversation(config, store=store, conversation_id="t1", mounts=MOUNTS)
        with caplog.at_level(logging.WARNING):
            response = await convo.send("hi")
        assert text_of(response.message) == "reply 0"
        assert "sessions document of 't1' was not written" in caplog.text
        assert await store.last_turn_number("t1") == 1


@pytest.mark.unit
class TestRegistration:
    async def test_eager_with_a_project_mount(self, store: FileStore) -> None:
        config, fake = _config(1)
        convo = Conversation(config, store=store, conversation_id="t1", mounts=MOUNTS)
        await convo.send("hi")
        assert _tools(fake)[-2:] == ["recall_turn", "search_history"]

    async def test_lazy_on_a_bare_scope(self, store: FileStore) -> None:
        config, fake = _config(1)
        convo = Conversation(
            config, store=store, conversation_id="t1", memory_scope="user:demo"
        )
        await convo.send("hi")
        assert not {"recall_turn", "search_history"} & set(_tools(fake))

    async def test_recall_tool_false_removes_the_pair(self, store: FileStore) -> None:
        config, fake = _config(1)
        convo = Conversation(
            config,
            store=store,
            conversation_id="t1",
            mounts=MOUNTS,
            compaction=CompactionConfig(recall_tool=False),
        )
        await convo.send("hi")
        assert not {"recall_turn", "search_history"} & set(_tools(fake))
