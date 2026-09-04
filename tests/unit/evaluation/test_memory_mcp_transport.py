"""The `mcp` transport leg of the harness (DESIGN §13.12, §25).

An mcp cell's tools are the memory server's own, consumed through
`McpServer.in_process` over the official client — every tool call
crosses the MCP wire the way a third-party agent consumes the daemon.
These tests pin that the wire carries a scenario end to end exactly as
the function transport does, that a corrective failure comes back
in-band, and that every session's client is closed when the cell ends.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from neosian._foundation.evaluation.expectations import parse_expectation
from neosian._foundation.evaluation.memory_runner import run_scenario
from neosian._foundation.evaluation.memory_types import (
    MemoryScenario,
    MemorySession,
    StoreExpectation,
    Transport,
)
from neosian._foundation.evaluation.types import EvalTurn
from neosian._foundation.llm.base import ToolCall
from neosian._foundation.llm.fake import FakeTurn
from neosian._foundation.mcp.client import McpServer
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.shared.types import (
    AgentConfig,
    Model,
    ToolCallId,
    ToolName,
)

_MOUNT = Mount(scope="user:eval", mount_path="user")


def _base() -> AgentConfig:
    return AgentConfig(
        system_prompt="agent under test",
        model=Model.FAKE,
        enable_todo=False,
    )


def _call(arguments: dict[str, object]) -> FakeTurn:
    return FakeTurn(
        tool_calls=(
            ToolCall(id=ToolCallId("c1"), name=ToolName("memory"), arguments=arguments),
        )
    )


def _turn(user: str, expect: dict[str, object] | None = None) -> EvalTurn:
    return EvalTurn(
        user=user,
        expect=parse_expectation(expect or {"tool": "memory"}, "test", "expect"),
    )


def _scenario(*sessions: MemorySession) -> MemoryScenario:
    return MemoryScenario(name="mcp-probe", sessions=sessions)


@pytest.mark.unit
class TestMcpTransport:
    async def test_two_sessions_carry_through_the_wire(self, tmp_path: Path) -> None:
        scenario = _scenario(
            MemorySession(
                name="record",
                turns=(_turn("Remember: espresso only."),),
                script=(
                    _call(
                        {
                            "command": "create",
                            "path": "/user/preferences",
                            "content": "Espresso only.",
                        }
                    ),
                    FakeTurn(content="Noted."),
                ),
                expect_store=StoreExpectation(counts={"/user": 1}),
            ),
            MemorySession(
                name="recall",
                turns=(
                    _turn(
                        "What do I drink?",
                        {"tool": "memory", "response": {"contains": "Espresso"}},
                    ),
                ),
                script=(
                    _call({"command": "view", "path": "/user/preferences"}),
                    FakeTurn(content="Espresso."),
                ),
            ),
        )
        result = await run_scenario(
            _base(),
            Transport.MCP,
            Model.FAKE,
            scenario,
            mounts=(_MOUNT,),
            store_root=tmp_path / "store",
        )
        assert result.passed, [f for t in result.turns for f in t.failures]
        assert result.variant == "mcp"
        captures = [c for t in result.turns for c in t.tool_calls]
        assert all(c.executed and c.ok for c in captures)
        doc = await FileStore(tmp_path / "store").read("user:eval", "preferences")
        assert doc is not None and doc.content == "Espresso only."

    async def test_corrective_failure_crosses_the_wire(self, tmp_path: Path) -> None:
        """A str_replace on a missing document comes back as the same
        failed-but-captured ToolResult the function transport produces —
        `is_error` read back into `ToolResult.fail`, never an exception."""
        scenario = _scenario(
            MemorySession(
                name="one",
                turns=(_turn("go", {"tool": "memory"}),),
                script=(
                    _call(
                        {
                            "command": "str_replace",
                            "path": "/user/missing",
                            "old_str": "a",
                            "new_str": "b",
                        }
                    ),
                    FakeTurn(content="done"),
                ),
            ),
        )
        result = await run_scenario(
            _base(),
            Transport.MCP,
            Model.FAKE,
            scenario,
            mounts=(_MOUNT,),
            store_root=tmp_path / "store",
        )
        (capture,) = [c for t in result.turns for c in t.tool_calls]
        assert capture.executed and not capture.ok

    async def test_every_session_server_is_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One connected server per session, each closed when the cell ends."""
        closes = 0
        real_aexit = McpServer.__aexit__

        async def counting_aexit(self: McpServer, *exc_info: object) -> None:
            nonlocal closes
            closes += 1
            await real_aexit(self, *exc_info)

        monkeypatch.setattr(McpServer, "__aexit__", counting_aexit)
        scenario = _scenario(
            MemorySession(
                name="a",
                turns=(_turn("go"),),
                script=(
                    _call({"command": "view", "path": "/"}),
                    FakeTurn(content="ok"),
                ),
            ),
            MemorySession(
                name="b",
                turns=(_turn("go"),),
                script=(
                    _call({"command": "view", "path": "/"}),
                    FakeTurn(content="ok"),
                ),
            ),
        )
        result = await run_scenario(
            _base(),
            Transport.MCP,
            Model.FAKE,
            scenario,
            mounts=(_MOUNT,),
            store_root=tmp_path / "store",
        )
        assert result.passed, [f for t in result.turns for f in t.failures]
        assert closes == 2

    async def test_function_and_mcp_produce_identical_store_state(
        self, tmp_path: Path
    ) -> None:
        def scenario() -> MemoryScenario:
            return _scenario(
                MemorySession(
                    name="write",
                    turns=(_turn("record"), _turn("refine")),
                    script=(
                        _call(
                            {
                                "command": "create",
                                "path": "/user/prefs",
                                "content": "a\nb",
                            }
                        ),
                        FakeTurn(content="ok"),
                        _call(
                            {
                                "command": "str_replace",
                                "path": "/user/prefs",
                                "old_str": "b",
                                "new_str": "c",
                            }
                        ),
                        FakeTurn(content="ok"),
                    ),
                ),
            )

        for transport, root in (
            (Transport.FUNCTION, tmp_path / "fn"),
            (Transport.MCP, tmp_path / "mcp"),
        ):
            result = await run_scenario(
                _base(),
                transport,
                Model.FAKE,
                scenario(),
                mounts=(_MOUNT,),
                store_root=root,
            )
            assert result.passed, [f for t in result.turns for f in t.failures]

        fn, mcp = FileStore(tmp_path / "fn"), FileStore(tmp_path / "mcp")
        fn_docs = await fn.list_documents("user:eval")
        mcp_docs = await mcp.list_documents("user:eval")
        assert [(d.path, d.version) for d in fn_docs] == [
            (d.path, d.version) for d in mcp_docs
        ]
        for entry in fn_docs:
            fn_doc = await fn.read("user:eval", entry.path)
            mcp_doc = await mcp.read("user:eval", entry.path)
            assert fn_doc is not None and mcp_doc is not None
            assert fn_doc.content == mcp_doc.content
        fn_rows = await fn.versions("user:eval", "prefs")
        mcp_rows = await mcp.versions("user:eval", "prefs")
        assert [(r.version, r.action, r.actor) for r in fn_rows] == [
            (r.version, r.action, r.actor) for r in mcp_rows
        ]


@pytest.mark.unit
def test_evaluation_import_does_not_load_the_mcp_extra() -> None:
    """The mcp transport's SDK import is lazy: the facade (and the
    transport module itself) import without the SDK — the extra is
    needed to run an mcp cell, never to import the harness."""
    code = (
        "import neosian.evaluation, sys; "
        "import neosian._foundation.evaluation.memory_mcp; "
        "assert 'mcp' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
