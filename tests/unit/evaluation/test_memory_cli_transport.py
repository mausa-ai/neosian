"""The `cli` transport leg of the harness (DESIGN §14.3, ledger #78).

A cli cell executes every memory call through the real `neosian memory`
engine in-process — argv grammar, a fresh store per call, the --json
envelope parsed back into a ToolResult — so these tests pin that the
shell surface carries a scenario end to end exactly as the function
transport does.
"""

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
from neosian._foundation.memory.file import FileStore
from neosian._foundation.shared.types import (
    AgentConfig,
    Model,
    ToolCallId,
    ToolName,
)

_MOUNT_ARGS = {"scope": "user:eval", "mount_path": "user"}


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
    return MemoryScenario(name="cli-probe", sessions=sessions)


@pytest.mark.unit
class TestCliTransport:
    async def test_two_sessions_carry_through_the_shell(self, tmp_path: Path) -> None:
        from neosian._foundation.memory.mounts import Mount

        mount = Mount(**_MOUNT_ARGS)  # type: ignore[arg-type]
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
            Transport.CLI,
            Model.FAKE,
            scenario,
            mounts=(mount,),
            store_root=tmp_path / "store",
        )
        assert result.passed, [f for t in result.turns for f in t.failures]
        assert result.variant == "cli"
        captures = [c for t in result.turns for c in t.tool_calls]
        assert all(c.executed and c.ok for c in captures)
        doc = await FileStore(tmp_path / "store").read("user:eval", "preferences")
        assert doc is not None and doc.content == "Espresso only."

    async def test_unknown_command_is_captured_not_raised(self, tmp_path: Path) -> None:
        """A script emitting a bad command yields a failed-but-captured
        ToolResult (the CLI's exit-2 answer, decoded) — never an
        exception out of the cell."""
        from neosian._foundation.memory.mounts import Mount

        scenario = _scenario(
            MemorySession(
                name="one",
                turns=(_turn("go", {"tool": "memory"}),),
                script=(
                    _call({"command": "update", "path": "/user/x"}),
                    FakeTurn(content="done"),
                ),
            ),
        )
        result = await run_scenario(
            _base(),
            Transport.CLI,
            Model.FAKE,
            scenario,
            mounts=(Mount(**_MOUNT_ARGS),),  # type: ignore[arg-type]
            store_root=tmp_path / "store",
        )
        (capture,) = [c for t in result.turns for c in t.tool_calls]
        assert capture.executed and not capture.ok

    async def test_function_and_cli_produce_identical_store_state(
        self, tmp_path: Path
    ) -> None:
        from neosian._foundation.memory.mounts import Mount

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

        mount = Mount(**_MOUNT_ARGS)  # type: ignore[arg-type]
        for transport, root in (
            (Transport.FUNCTION, tmp_path / "fn"),
            (Transport.CLI, tmp_path / "cli"),
        ):
            result = await run_scenario(
                _base(),
                transport,
                Model.FAKE,
                scenario(),
                mounts=(mount,),
                store_root=root,
            )
            assert result.passed, [f for t in result.turns for f in t.failures]

        fn, cli = FileStore(tmp_path / "fn"), FileStore(tmp_path / "cli")
        fn_docs = await fn.list_documents("user:eval")
        cli_docs = await cli.list_documents("user:eval")
        assert [(d.path, d.version) for d in fn_docs] == [
            (d.path, d.version) for d in cli_docs
        ]
        for entry in fn_docs:
            fn_doc = await fn.read("user:eval", entry.path)
            cli_doc = await cli.read("user:eval", entry.path)
            assert fn_doc is not None and cli_doc is not None
            assert fn_doc.content == cli_doc.content
        fn_rows = await fn.versions("user:eval", "prefs")
        cli_rows = await cli.versions("user:eval", "prefs")
        assert [(r.version, r.action) for r in fn_rows] == [
            (r.version, r.action) for r in cli_rows
        ]
