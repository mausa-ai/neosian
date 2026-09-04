"""The tool-approval gate (NT, DESIGN §17, ledger #105–#107).

A dangerous tool call pauses for approval and resumes/denies correctly
on both paths — the phase done-when, fully keyless. Denials are in-band
`ToolResult.fail`s the model sees; no decision (timeout, approver
exception, invalid return) denies, always. The wire vocabulary stays at
ten events (ledger #106) — the pause shows as `tool_progress`, the
outcome rides `tool_result`.
"""

import asyncio
from unittest.mock import patch

import pytest

from neosian import (
    Agent,
    AgentConfig,
    AgentHooks,
    AgentResponse,
    Model,
    ToolApprovalRequest,
    ToolDecision,
    ToolGateConfig,
)
from neosian._foundation.agent.events import (
    AgentEvent,
    DoneEvent,
    MemoryWriteEvent,
    ToolProgressEvent,
    ToolResultEvent,
)
from neosian._foundation.agent.hooks import ToolEvent, TurnEvent
from neosian._foundation.llm.base import Message, Role, ToolCall
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.types import (
    ToolCallId,
    ToolFunction,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult

_SYSTEM = "You are a test agent."
_USER = [Message(role=Role.USER, content="launch it")]
_HEARTBEAT = "neosian._foundation.agent.tool_exec.Streaming.HEARTBEAT_INTERVAL_SECONDS"


def _make_tool(executions: list[str]) -> ToolFunction:
    """A fresh 'dangerous' tool per test; `executions` records real runs."""

    @Tool(name=ToolName("launch"), description="Launch something dangerous")
    async def launch(target: str) -> ToolResult[str]:
        executions.append(target)
        return ToolResult.ok(f"launched {target}")

    return launch


def _script() -> FakeScript:
    return FakeScript(
        turns=(
            FakeTurn(
                tool_calls=(
                    ToolCall(
                        id=ToolCallId("c1"),
                        name=ToolName("launch"),
                        arguments={"target": "probe"},
                    ),
                )
            ),
            FakeTurn(content="finished"),
        )
    )


def _agent(
    gate: ToolGateConfig | None,
    executions: list[str],
    *,
    script: FakeScript | None = None,
    hooks: AgentHooks | None = None,
) -> Agent:
    fake = FakeClient(script or _script())
    return Agent(
        AgentConfig(
            system_prompt=_SYSTEM,
            model=Model.FAKE,
            enable_todo=False,
            tools=[_make_tool(executions)],
            tool_gate=gate,
            hooks=hooks,
            client_factory=lambda _: fake,
        )
    )


async def _events(agent: Agent) -> list[AgentEvent]:
    return [event async for event in await agent.run(_USER, stream=True)]


def _approve_all(_request: ToolApprovalRequest) -> ToolDecision:
    return ToolDecision(approved=True)


class TestApproval:
    async def test_approved_call_executes_blocking(self) -> None:
        executions: list[str] = []
        gate = ToolGateConfig(approver=_approve_all)
        response = await _agent(gate, executions).run(_USER, stream=False)
        assert executions == ["probe"]
        assert response.message.content == "finished"

    async def test_approved_call_executes_streaming(self) -> None:
        executions: list[str] = []

        async def approver(_request: ToolApprovalRequest) -> ToolDecision:
            return ToolDecision(approved=True)

        events = await _events(_agent(ToolGateConfig(approver=approver), executions))
        assert executions == ["probe"]
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert result.success

    async def test_approver_sees_the_call(self) -> None:
        requests: list[ToolApprovalRequest] = []

        def approver(request: ToolApprovalRequest) -> ToolDecision:
            requests.append(request)
            return ToolDecision(approved=True)

        await _agent(ToolGateConfig(approver=approver), []).run(_USER, stream=False)
        (request,) = requests
        assert request.call_id == ToolCallId("c1")
        assert request.name == ToolName("launch")
        assert request.arguments == {"target": "probe"}

    async def test_hook_sequence_identical_gated_vs_ungated(self) -> None:
        def run_types(hook_events: list[object]) -> list[type]:
            return [type(e) for e in hook_events]

        gated: list[object] = []
        ungated: list[object] = []
        hooks_g = AgentHooks(on_turn=gated.append, on_tool=gated.append, strict=True)
        hooks_u = AgentHooks(
            on_turn=ungated.append, on_tool=ungated.append, strict=True
        )
        await _agent(ToolGateConfig(approver=_approve_all), [], hooks=hooks_g).run(
            _USER, stream=False
        )
        await _agent(None, [], hooks=hooks_u).run(_USER, stream=False)
        assert run_types(gated) == run_types(ungated)


class TestDenial:
    async def test_denied_blocking_is_corrective_not_executed(self) -> None:
        executions: list[str] = []

        def deny(_request: ToolApprovalRequest) -> ToolDecision:
            return ToolDecision(approved=False, reason="not today")

        response = await _agent(ToolGateConfig(approver=deny), executions).run(
            _USER, stream=False
        )
        assert executions == []
        # The model saw the denial in-band and adapted (second turn ran).
        assert response.message.content == "finished"
        (tool_msg,) = [m for m in response.turn_messages if m.role == Role.TOOL]
        assert isinstance(tool_msg.content, str)
        assert "denied by the approval gate: not today" in tool_msg.content

    async def test_denied_streaming_rides_tool_result(self) -> None:
        executions: list[str] = []

        def deny(_request: ToolApprovalRequest) -> ToolDecision:
            return ToolDecision(approved=False, reason="not today")

        events = await _events(_agent(ToolGateConfig(approver=deny), executions))
        assert executions == []
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert not result.success
        assert result.error is not None
        assert "denied by the approval gate: not today" in result.error
        # The run terminates normally — a denial is feedback, not a block.
        assert any(isinstance(e, DoneEvent) for e in events)

    async def test_denied_without_reason(self) -> None:
        def deny(_request: ToolApprovalRequest) -> ToolDecision:
            return ToolDecision(approved=False)

        events = await _events(_agent(ToolGateConfig(approver=deny), []))
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert result.error == "Tool 'launch' denied by the approval gate"

    async def test_on_tool_fires_with_the_denial(self) -> None:
        seen: list[object] = []
        hooks = AgentHooks(on_tool=seen.append, strict=True)

        def deny(_request: ToolApprovalRequest) -> ToolDecision:
            return ToolDecision(approved=False)

        await _agent(ToolGateConfig(approver=deny), [], hooks=hooks).run(
            _USER, stream=False
        )
        (event,) = seen
        assert isinstance(event, ToolEvent)
        assert not event.result.success

    async def test_turn_messages_parity_blocking_vs_streaming(self) -> None:
        def deny(_request: ToolApprovalRequest) -> ToolDecision:
            return ToolDecision(approved=False, reason="no")

        captured: list[TurnEvent] = []
        hooks = AgentHooks(on_turn=captured.append, strict=True)
        await _agent(ToolGateConfig(approver=deny), [], hooks=hooks).run(
            _USER, stream=False
        )
        await _events(_agent(ToolGateConfig(approver=deny), [], hooks=hooks))
        blocking, streaming = captured
        assert [(m.role, m.content) for m in blocking.response.turn_messages] == [
            (m.role, m.content) for m in streaming.response.turn_messages
        ]

    async def test_denied_memory_write_emits_no_frame(self, tmp_path: object) -> None:
        from pathlib import Path

        from neosian._foundation.memory.file import FileStore
        from neosian._foundation.memory.mounts import MemoryConfig, Mount

        assert isinstance(tmp_path, Path)
        memory = MemoryConfig(
            store=FileStore(tmp_path), mounts=(Mount(scope="user:t", mount_path="m"),)
        )
        script = FakeScript(
            turns=(
                FakeTurn(
                    tool_calls=(
                        ToolCall(
                            id=ToolCallId("c1"),
                            name=ToolName("memory"),
                            arguments={
                                "command": "create",
                                "path": "/m/a",
                                "content": "x",
                            },
                        ),
                    )
                ),
                FakeTurn(content="ok"),
            )
        )

        def deny(_request: ToolApprovalRequest) -> ToolDecision:
            return ToolDecision(approved=False)

        fake = FakeClient(script)
        agent = Agent(
            AgentConfig(
                system_prompt=_SYSTEM,
                model=Model.FAKE,
                enable_todo=False,
                memory=memory,
                tool_gate=ToolGateConfig(approver=deny),
                client_factory=lambda _: fake,
            )
        )
        events = [event async for event in await agent.run(_USER, stream=True)]
        assert [e for e in events if isinstance(e, MemoryWriteEvent)] == []
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert not result.success


class TestDefaultDeny:
    async def test_timeout_denies(self) -> None:
        executions: list[str] = []

        async def never(_request: ToolApprovalRequest) -> ToolDecision:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        gate = ToolGateConfig(approver=never, timeout_seconds=0.05)
        events = await _events(_agent(gate, executions))
        assert executions == []
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert not result.success
        assert result.error is not None
        assert "approval timed out after 0.05s" in result.error
        assert "default-deny" in result.error

    async def test_approver_exception_denies(self) -> None:
        def boom(_request: ToolApprovalRequest) -> ToolDecision:
            raise RuntimeError("approver broke")

        events = await _events(_agent(ToolGateConfig(approver=boom), []))
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert not result.success
        assert result.error is not None
        assert "approver failed: approver broke" in result.error
        assert "default-deny" in result.error

    async def test_invalid_return_denies(self) -> None:
        events = await _events(
            _agent(ToolGateConfig(approver=lambda _request: "yes"), [])  # type: ignore[arg-type,return-value]
        )
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert not result.success
        assert result.error is not None
        assert "returned str, not ToolDecision" in result.error

    async def test_no_timeout_waits(self) -> None:
        async def slow(_request: ToolApprovalRequest) -> ToolDecision:
            await asyncio.sleep(0)
            return ToolDecision(approved=True)

        executions: list[str] = []
        gate = ToolGateConfig(approver=slow, timeout_seconds=None)
        await _agent(gate, executions).run(_USER, stream=False)
        assert executions == ["probe"]


class TestPauseResume:
    """The phase done-when: the call pauses, the pause is wire-visible,
    and the run resumes (or denies) correctly on both paths."""

    async def test_streaming_pause_shows_progress_then_resumes(self) -> None:
        release = asyncio.Event()
        executions: list[str] = []

        async def approver(_request: ToolApprovalRequest) -> ToolDecision:
            await release.wait()
            return ToolDecision(approved=True)

        agent = _agent(
            ToolGateConfig(approver=approver, timeout_seconds=None), executions
        )
        events: list[AgentEvent] = []
        with patch(_HEARTBEAT, 0.02):
            stream = await agent.run(_USER, stream=True)
            async for event in stream:
                events.append(event)
                # The pause is wire-visible: a progress frame arrives
                # while the approver is still pending — then we approve.
                if isinstance(event, ToolProgressEvent) and not release.is_set():
                    assert executions == []
                    release.set()
        assert any(isinstance(e, ToolProgressEvent) for e in events)
        (result,) = [e for e in events if isinstance(e, ToolResultEvent)]
        assert result.success
        assert executions == ["probe"]
        assert any(isinstance(e, DoneEvent) for e in events)

    @pytest.mark.parametrize("entry", ["agent", "session"])
    @pytest.mark.parametrize("approved", [True, False])
    async def test_blocking_pause_resolves_on_both_entry_points(
        self, entry: str, approved: bool
    ) -> None:
        release = asyncio.Event()
        executions: list[str] = []

        async def approver(_request: ToolApprovalRequest) -> ToolDecision:
            await release.wait()
            return ToolDecision(approved=approved, reason="ruled")

        agent = _agent(
            ToolGateConfig(approver=approver, timeout_seconds=None), executions
        )

        async def run() -> AgentResponse:
            if entry == "agent":
                return await agent.run(_USER, stream=False)
            async with agent.session() as session:
                return await session.run(_USER, stream=False)

        task = asyncio.create_task(run())
        await asyncio.sleep(0.05)
        assert not task.done()  # paused, awaiting approval
        assert executions == []
        release.set()
        response = await task
        assert executions == (["probe"] if approved else [])
        assert response.message.content == "finished"


class TestConfigValidation:
    def test_default_timeout_is_sixty_seconds(self) -> None:
        assert ToolGateConfig(approver=_approve_all).timeout_seconds == 60.0

    def test_non_positive_timeout_refused(self) -> None:
        with pytest.raises(ValueError, match="must be positive or None"):
            ToolGateConfig(approver=_approve_all, timeout_seconds=0)
        with pytest.raises(ValueError, match="must be positive or None"):
            ToolGateConfig(approver=_approve_all, timeout_seconds=-1)

    def test_non_callable_approver_refused(self) -> None:
        with pytest.raises(ValueError, match="must be callable"):
            ToolGateConfig(approver="not callable")  # type: ignore[arg-type]

    async def test_unknown_tool_bypasses_approver(self) -> None:
        requests: list[ToolApprovalRequest] = []

        def approver(request: ToolApprovalRequest) -> ToolDecision:
            requests.append(request)
            return ToolDecision(approved=True)

        script = FakeScript(
            turns=(
                FakeTurn(
                    tool_calls=(
                        ToolCall(
                            id=ToolCallId("c1"),
                            name=ToolName("no_such_tool"),
                            arguments={},
                        ),
                    )
                ),
                FakeTurn(content="ok"),
            )
        )
        await _agent(ToolGateConfig(approver=approver), [], script=script).run(
            _USER, stream=False
        )
        assert requests == []
