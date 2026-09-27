"""The search_history tool (§32, #298): hits and their rendering, the
bound, the corrective failures, the reach, the server's twin."""

from __future__ import annotations

import pytest

from neosian import tool_definition
from neosian._foundation.conversation.recall import reach_reminder
from neosian._foundation.conversation.search_history import (
    MAX_HITS,
    create_search_any_tool,
    create_search_history_tool,
    history_any_tools,
    history_tools,
)
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.types import ToolCallId, ToolName


async def _say(store: FileStore, conversation: str, text: str) -> None:
    await store.append_turn(
        conversation,
        (
            Message(role=Role.USER, content=text),
            Message(role=Role.ASSISTANT, content=f"re: {text}"),
        ),
    )


async def _call(store: FileStore, conversation: str, name: str, arg: str) -> None:
    call = ToolCall(id=ToolCallId("c1"), name=ToolName(name), arguments={"q": arg})
    await store.append_turn(
        conversation,
        (
            Message(role=Role.USER, content="do it"),
            Message(role=Role.ASSISTANT, content=None, tool_calls=[call]),
            Message(role=Role.TOOL, content=f"result for {arg}", tool_call_id=call.id),
        ),
    )


@pytest.mark.unit
class TestTheSearch:
    async def test_hits_text_arguments_and_results_newest_first(
        self, store: FileStore
    ) -> None:
        await _say(store, "t1", "the pelican flew")
        await _call(store, "t1", "lookup", "pelican island")
        await _say(store, "t1", "nothing here")
        tool = create_search_history_tool(store, "t1")
        result = await tool(query="pelican")
        assert result.success and result.data is not None
        lines = result.data.splitlines()
        assert (
            lines[0].startswith("[t1 #2] ")
            and lines[1] == '  lookup {"q":"pelican island"}'
        )
        assert lines[2].startswith("[t1 #1] ") and lines[3] == "  the pelican flew"
        assert lines[-1].startswith("[newest first, at most 10; call recall_turn(")
        answered = await tool(query="result")
        assert (
            answered.data is not None and "  result for pelican island" in answered.data
        )

    async def test_no_hit_is_an_answer(self, store: FileStore) -> None:
        await _say(store, "t1", "one")
        result = await create_search_history_tool(store, "t1")(query="Two Three")
        assert result.success
        assert result.data == (
            "No turn matches every term: 'two', 'three'. Try fewer or different terms."
        )

    async def test_the_bound_is_clamped_and_said(self, store: FileStore) -> None:
        for i in range(3):
            await _say(store, "t1", f"tick {i}")
        tool = create_search_history_tool(store, "t1")
        result = await tool(query="tick", limit=MAX_HITS + 10)
        assert result.success and result.data is not None
        assert f"at most {MAX_HITS};" in result.data
        result = await tool(query="tick", limit=2)
        assert result.data is not None and result.data.count("[t1 #") == 2
        assert "[t1 #0]" not in result.data and "[t1 #3]" in result.data

    @pytest.mark.parametrize(
        ("arguments", "error"),
        [
            ({"query": "   "}, "Give at least one search term"),
            ({"query": "x", "limit": 0}, "limit must be at least 1; got 0"),
        ],
    )
    async def test_misuse_fails_correctively(
        self, store: FileStore, arguments: dict[str, object], error: str
    ) -> None:
        result = await create_search_history_tool(store, "t1")(**arguments)
        assert not result.success and result.error == error

    async def test_an_invalid_id_is_the_store_error(self, store: FileStore) -> None:
        result = await create_search_any_tool(store)(query="x", conversation="a b")
        assert not result.success and result.error is not None
        assert result.error.startswith("[agent_conversation_id_invalid]")


@pytest.mark.unit
class TestTheReach:
    async def test_none_is_the_whole_reach_and_a_name_narrows(
        self, store: FileStore
    ) -> None:
        await _say(store, "own", "shared word")
        await _say(store, "viewed", "shared word")
        await _say(store, "other", "shared word")
        tool = create_search_history_tool(store, "own", addressable=["viewed"])
        both = await tool(query="shared")
        assert both.data is not None and both.data.count("[") == 3  # two hits + footer
        assert "[other #" not in both.data
        one = await tool(query="shared", conversation="viewed")
        assert one.data is not None and one.data.count("[viewed #") == 1

    async def test_a_stranger_fails_correctively(self, store: FileStore) -> None:
        tool = create_search_history_tool(store, "own", addressable=["viewed"])
        result = await tool(query="x", conversation="zzz")
        assert not result.success
        assert result.system_reminder == (
            "search_history reaches this conversation and the ones shown as "
            "views: 'own', 'viewed'."
        )

    async def test_the_pair_reads_the_project_live_and_deduplicates(
        self, store: FileStore
    ) -> None:
        listed: list[str] = ["a"]

        async def sessions() -> list[str]:
            return list(listed)

        recall, search = history_tools(store, "own", views=["a"], sessions=sessions)
        refused = await search(query="x", conversation="s1")
        assert refused.system_reminder == (
            "search_history reaches this conversation, the ones shown as views "
            "and the project's listed sessions: 'own', 'a'."
        )
        listed.append("s1")
        await _say(store, "s1", "landed later")
        found = await search(query="landed", conversation="s1")
        assert found.success and found.data is not None and "[s1 #1]" in found.data
        opened = await recall(turn=1, conversation="s1")
        assert opened.success and opened.data is not None
        assert "USER: landed later" in opened.data

    def test_a_long_reach_is_capped_in_the_reminder(self) -> None:
        text = reach_reminder("recall_turn", "here", [f"c{i}" for i in range(15)])
        assert text.endswith("'c11' and 3 more.") and "'c12'" not in text


@pytest.mark.unit
class TestTheServersTwin:
    async def test_store_wide_then_narrowed(self, store: FileStore) -> None:
        await _say(store, "a", "needle one")
        await _say(store, "b", "needle two")
        _, search = history_any_tools(store)
        assert tool_definition(search).name == "search_history"
        wide = await search(query="needle")
        assert wide.data is not None and "[a #1]" in wide.data and "[b #1]" in wide.data
        narrow = await search(query="needle", conversation="a")
        assert narrow.data is not None and "[b #1]" not in narrow.data
