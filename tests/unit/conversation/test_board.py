"""The board (§21, ledger #136): a memory mount at `/board` on the scope
the caller names verbatim, composing with any memory; shared by naming
the same scope — over one FileStore and through the daemon."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from neosian import AgentConfig, Model
from neosian._foundation.conversation.core import Conversation
from neosian._foundation.llm.base import ToolCall, text_of
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig, Mount
from neosian._foundation.server.app import build_app
from neosian._foundation.server.remote import RemoteStore
from neosian._foundation.shared.types import ToolCallId, ToolName

_BOARD = "task:42"


def _config(*turns: FakeTurn, **kwargs: Any) -> tuple[AgentConfig, FakeClient]:
    fake = FakeClient(FakeScript(turns=turns))
    config = AgentConfig(
        system_prompt="You are a test agent.",
        model=Model.FAKE,
        enable_todo=False,
        client_factory=lambda _: fake,
        **kwargs,
    )
    return config, fake


def _memory(command: str, path: str, content: str | None = None) -> FakeTurn:
    arguments: dict[str, Any] = {"command": command, "path": path}
    if content is not None:
        arguments["content"] = content
    return FakeTurn(
        tool_calls=(
            ToolCall(id=ToolCallId("c1"), name=ToolName("memory"), arguments=arguments),
        )
    )


def _seen(fake: FakeClient, call: int = -1) -> str:
    return "\n".join(text_of(m) for m in fake.calls[call].messages)


@pytest.mark.unit
class TestTheBoardMount:
    async def test_board_alone_mounts_at_board(self, store: FileStore) -> None:
        config, fake = _config(
            _memory("create", "/board/plan", "step 1"), FakeTurn(content="noted")
        )
        convo = Conversation(config, store=store, conversation_id="a", board=_BOARD)
        await convo.send("start")
        document = await store.read(_BOARD, "plan")
        assert document is not None and document.content == "step 1"
        assert document.actor == "conv:a#1"
        assert "## /board" in _seen(fake, 0)
        assert "## /memories" not in _seen(fake, 0)

    async def test_board_composes_with_memory_scope(self, store: FileStore) -> None:
        config, fake = _config(FakeTurn(content="ok"))
        convo = Conversation(
            config,
            store=store,
            conversation_id="a",
            memory_scope="user:1",
            board=_BOARD,
        )
        await convo.send("hi")
        index = _seen(fake)
        assert index.index("## /memories") < index.index("## /board")

    async def test_board_lands_on_the_config_memory_store(
        self, store: FileStore, tmp_path: Path
    ) -> None:
        other = FileStore(tmp_path / "other")
        config, _ = _config(
            _memory("create", "/board/plan", "x"),
            FakeTurn(content="ok"),
            memory=MemoryConfig(
                store=other, mounts=(Mount(scope="user:1", mount_path="memories"),)
            ),
        )
        convo = Conversation(config, store=store, conversation_id="a", board=_BOARD)
        await convo.send("go")
        assert await other.read(_BOARD, "plan") is not None
        assert await store.read(_BOARD, "plan") is None

    async def test_a_second_board_path_is_refused(self, store: FileStore) -> None:
        config, _ = _config(FakeTurn(content="ok"))
        with pytest.raises(ValueError, match="unique"):
            Conversation(
                config,
                store=store,
                conversation_id="a",
                mounts=[Mount(scope="user:1", mount_path="board")],
                board=_BOARD,
            )


@pytest.mark.unit
class TestTwoAgentsOneBoard:
    async def test_the_second_agent_reads_what_the_first_wrote(
        self, store: FileStore
    ) -> None:
        config_a, _ = _config(
            _memory("create", "/board/plan", "A owns the parser"),
            FakeTurn(content="written"),
        )
        await Conversation(
            config_a, store=store, conversation_id="a", board=_BOARD
        ).send("claim")

        config_b, fake_b = _config(
            _memory("view", "/board/plan"), FakeTurn(content="seen")
        )
        await Conversation(
            config_b, store=store, conversation_id="b", board=_BOARD
        ).send("what is A doing?")
        assert "/board/plan" in _seen(fake_b, 0)  # in B's frozen index
        assert "A owns the parser" in _seen(fake_b, 1)  # the view result
        (row,) = await store.versions(_BOARD, "plan")
        assert row.actor == "conv:a#1"

    async def test_the_edit_only_recipe_is_the_old_update_only_contract(
        self, store: FileStore
    ) -> None:
        await store.write(_BOARD, "status", "idle", actor="host")
        config, fake = _config(
            _memory("create", "/board/status", "busy"),
            _memory("create", "/board/new", "x"),
            FakeTurn(content="done"),
        )
        convo = Conversation(
            config,
            store=store,
            conversation_id="a",
            mounts=[Mount(scope=_BOARD, mount_path="board", edit_only=True)],
        )
        await convo.send("update")
        status = await store.read(_BOARD, "status")
        assert status is not None and status.content == "busy"
        assert await store.read(_BOARD, "new") is None
        assert "[memory_edit_only_mount]" in _seen(fake, 2)


@pytest.mark.unit
class TestThroughTheDaemon:
    async def test_the_board_write_carries_the_client_prefix(
        self, tmp_path: Path
    ) -> None:
        backing = FileStore(tmp_path / "backing")
        app = await build_app(backing, token="t")
        remote = await RemoteStore.connect(
            "http://state-process", token="t", transport=httpx.ASGITransport(app=app)
        )
        config, _ = _config(
            _memory("create", "/board/plan", "via the wire"), FakeTurn(content="ok")
        )
        await Conversation(
            config, store=remote, conversation_id="a", board=_BOARD
        ).send("go")
        document = await backing.read(_BOARD, "plan")
        assert document is not None and document.content == "via the wire"
        assert document.actor == "client:default/conv:a#1"
        (turn,) = await backing.read_turns("a")
        assert turn.actor == "client:default/conv:a"
        await remote.aclose()
