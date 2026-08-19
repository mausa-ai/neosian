"""Agent end-to-end on FakeProvider — fully keyless (ECOSYSTEM §7).

These are the tests the fake exists for: real Agent code paths (blocking,
streaming, tool loop, fallback, sessions) with zero API keys and zero
ad-hoc mocks.
"""

import os
from unittest.mock import patch

import pytest

from neosian import (
    Agent,
    AgentConfig,
    AgentHooks,
    ContentEvent,
    DoneEvent,
    FallbackConfig,
    FallbackEvent,
    LlmCallEvent,
    Model,
    ReasoningEffort,
    ToolEvent,
    TurnEvent,
)
from neosian._foundation.llm.base import (
    ImageBlock,
    Message,
    ModelUsage,
    Role,
    TextBlock,
    ToolCall,
    Usage,
)
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import ModelFailedError
from neosian._foundation.shared.types import (
    Provider,
    SystemPrompt,
    ToolCallId,
    ToolName,
)
from neosian._foundation.tools.base import Tool, ToolResult

_SYSTEM = SystemPrompt("You are a test agent.")
_USER = [Message(role=Role.USER, content="Hi")]


def _config(**overrides: object) -> AgentConfig:
    defaults: dict[str, object] = {
        "system_prompt": _SYSTEM,
        "model": Model.FAKE,
        "enable_todo": False,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestKeylessBoot:
    async def test_blocking_run_with_zero_keys(self) -> None:
        """The three-line keyless boot: config, agent, run."""
        with patch.dict(os.environ, {}, clear=True):
            agent = Agent(_config())
            response = await agent.run(_USER, stream=False)
        assert response.message.content == "fake response"
        assert response.usage == Usage(input_tokens=10, output_tokens=5)
        assert response.model == Model.FAKE.value

    async def test_streaming_run_with_zero_keys(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            agent = Agent(_config())
            events = [event async for event in await agent.run(_USER, stream=True)]
        assert events
        assert any(
            isinstance(event, ContentEvent) and "fake" in event.content
            for event in events
        )


@pytest.mark.unit
class TestClientFactory:
    async def test_factory_bypasses_the_router(self) -> None:
        fake = FakeClient()
        seen: list[Provider] = []

        def factory(provider: Provider) -> FakeClient:
            seen.append(provider)
            return fake

        agent = Agent(_config(client_factory=factory))
        response = await agent.run(_USER, stream=False)
        assert response.message.content == "fake response"
        assert seen == [Provider.FAKE]
        assert len(fake.calls) == 1

    async def test_tool_loop_end_to_end(self) -> None:
        @Tool(name="greet", description="Say hello")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello {name}")

        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(
                        tool_calls=(
                            ToolCall(
                                id=ToolCallId("c1"),
                                name=ToolName("greet"),
                                arguments={"name": "ada"},
                            ),
                        ),
                        usage=Usage(input_tokens=10, output_tokens=5),
                    ),
                    FakeTurn(
                        content="Greeted ada",
                        usage=Usage(input_tokens=30, output_tokens=4),
                    ),
                )
            )
        )
        agent = Agent(_config(tools=[greet], client_factory=lambda _: fake))
        response = await agent.run(_USER, stream=False)

        assert response.message.content == "Greeted ada"
        assert [tc.name for tc in response.tool_calls_made] == ["greet"]
        assert response.tool_results[0].data == "Hello ada"
        # The second recorded call must carry the TOOL message of the loop.
        second_call_roles = [m.role for m in fake.calls[1].messages]
        assert Role.TOOL in second_call_roles
        # Usage sums across iterations.
        assert response.usage == Usage(input_tokens=40, output_tokens=9)

    async def test_fallback_run_with_hand_computed_cost(self) -> None:
        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(error=TimeoutError("main model down")),
                    FakeTurn(
                        content="recovered",
                        usage=Usage(input_tokens=100, output_tokens=50),
                    ),
                )
            )
        )
        agent = Agent(
            _config(
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda _: fake,
            )
        )
        response = await agent.run(_USER, stream=False)
        assert response.message.content == "recovered"
        assert response.model == Model.FAKE_SMALL.value
        # 100×100_000 + 50×1_000_000 = 60_000_000 → 60 µ$ at FAKE_SMALL rates.
        assert response.usage.cost_micro_usd(Model.FAKE_SMALL) == 60

    async def test_reasoning_effort_dropped_on_non_reasoning_fallback(self) -> None:
        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(error=TimeoutError("main model down")),
                    FakeTurn(content="fallback answer"),
                )
            )
        )
        agent = Agent(
            _config(
                model=Model.FAKE_REASONING,
                reasoning_effort=ReasoningEffort.LOW,
                fallback=FallbackConfig(model=Model.FAKE),
                client_factory=lambda _: fake,
            )
        )
        await agent.run(_USER, stream=False)
        assert fake.calls[0].reasoning_effort is ReasoningEffort.LOW
        assert fake.calls[1].reasoning_effort is None  # silently dropped


@pytest.mark.unit
class TestCapabilityAwareFallback:
    async def test_media_conversation_skips_incapable_fallback(self) -> None:
        """FAKE carries media, FAKE_SMALL does not — no downgrade allowed."""
        fake = FakeClient(
            FakeScript(turns=(FakeTurn(error=TimeoutError("main down")),))
        )
        agent = Agent(
            _config(
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda _: fake,
            )
        )
        image_conversation = [
            Message(
                role=Role.USER,
                content=[
                    TextBlock(text="What is this?"),
                    ImageBlock(media_type="image/png", data="aGk="),
                ],
            )
        ]
        with pytest.raises(ModelFailedError) as exc_info:
            await agent.run(image_conversation, stream=False)
        assert exc_info.value.has_fallback is False

    async def test_text_conversation_still_falls_back(self) -> None:
        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(error=TimeoutError("main down")),
                    FakeTurn(content="fallback ok"),
                )
            )
        )
        agent = Agent(
            _config(
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda _: fake,
            )
        )
        response = await agent.run(_USER, stream=False)
        assert response.message.content == "fallback ok"


@pytest.mark.unit
class TestSessionWithFake:
    async def test_session_honors_factory_and_caches_per_provider(self) -> None:
        created: list[Provider] = []

        def factory(provider: Provider) -> FakeClient:
            created.append(provider)
            return FakeClient()

        agent = Agent(_config(client_factory=factory))
        async with agent.session() as session:
            first = await session.run(_USER, stream=False)
            second = await session.run(_USER, stream=False)
        assert first.message.content == second.message.content == "fake response"
        # One client per provider for the whole session.
        assert created == [Provider.FAKE]

    async def test_session_close_closes_the_fake(self) -> None:
        fake = FakeClient()
        agent = Agent(_config(client_factory=lambda _: fake))
        async with agent.session() as session:
            await session.run(_USER, stream=False)
        assert fake.closed is True


def _greet_script() -> FakeScript:
    """One tool round, then a final answer — the canonical two-turn loop."""
    return FakeScript(
        turns=(
            FakeTurn(
                tool_calls=(
                    ToolCall(
                        id=ToolCallId("c1"),
                        name=ToolName("greet"),
                        arguments={"name": "ada"},
                    ),
                ),
                usage=Usage(input_tokens=10, output_tokens=5),
            ),
            FakeTurn(
                content="Greeted ada",
                usage=Usage(input_tokens=30, output_tokens=4),
            ),
        )
    )


@Tool(name="greet", description="Say hello")
async def _greet(name: str) -> ToolResult[str]:
    return ToolResult.ok(f"Hello {name}")


@pytest.mark.unit
class TestTurnCapture:
    """AgentResponse.turn_messages per the DESIGN §3 contract."""

    async def test_last_element_is_response_message_by_identity(self) -> None:
        fake = FakeClient(_greet_script())
        agent = Agent(_config(tools=[_greet], client_factory=lambda _: fake))
        response = await agent.run(_USER, stream=False)
        assert response.turn_messages[-1] is response.message

    async def test_turn_messages_replay_as_valid_history(self) -> None:
        """input + turn_messages is a well-formed provider sequence."""
        fake = FakeClient(_greet_script())
        agent = Agent(_config(tools=[_greet], client_factory=lambda _: fake))
        response = await agent.run(_USER, stream=False)

        roles = [m.role for m in response.turn_messages]
        assert roles == [Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
        # The TOOL message answers the assistant's tool call, id-for-id.
        assistant_with_tools = response.turn_messages[0]
        tool_message = response.turn_messages[1]
        assert assistant_with_tools.tool_calls is not None
        assert tool_message.tool_call_id == assistant_with_tools.tool_calls[0].id

        # And the replay is accepted verbatim as next-call history.
        replay_fake = FakeClient()
        replay_agent = Agent(_config(client_factory=lambda _: replay_fake))
        history = [*_USER, *response.turn_messages]
        await replay_agent.run(
            [*history, Message(role=Role.USER, content="And again?")],
            stream=False,
        )
        recorded = replay_fake.calls[0].messages
        # System + replayed history + new user turn, order preserved.
        assert [m.role for m in recorded[1:5]] == [
            Role.USER,
            Role.ASSISTANT,
            Role.TOOL,
            Role.ASSISTANT,
        ]

    async def test_simple_run_turn_messages_is_just_the_answer(self) -> None:
        agent = Agent(_config(client_factory=lambda _: FakeClient()))
        response = await agent.run(_USER, stream=False)
        assert response.turn_messages == (response.message,)


@pytest.mark.unit
class TestUsageByModelAndErrorUsage:
    """Per-model usage attribution + billed usage riding terminal errors."""

    def _failing_then_fallback_agent(self) -> tuple[Agent, FakeClient]:
        fake = FakeClient(
            FakeScript(
                turns=(
                    _greet_script().turns[0],  # main: one billed tool round
                    FakeTurn(error=TimeoutError("main died mid-loop")),
                    FakeTurn(
                        content="fallback answer",
                        usage=Usage(input_tokens=100, output_tokens=50),
                    ),
                )
            )
        )
        agent = Agent(
            _config(
                tools=[_greet],
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda _: fake,
            )
        )
        return agent, fake

    async def test_fallback_history_is_not_polluted_by_failed_main(self) -> None:
        """Regression (DESIGN §3 register #5): the failed main attempt's
        partial tool round must not leak into the fallback's history."""
        agent, fake = self._failing_then_fallback_agent()
        await agent.run(_USER, stream=False)
        # calls: [main iter1, main iter2 (dies), fallback]
        fallback_call = fake.calls[2]
        assert [m.role for m in fallback_call.messages] == [Role.SYSTEM, Role.USER]

    async def test_usage_by_model_splits_across_attempts(self) -> None:
        """The failed main attempt's billed tokens survive into the response,
        attributed to the main model beside the fallback's."""
        agent, _ = self._failing_then_fallback_agent()
        response = await agent.run(_USER, stream=False)
        assert [(entry.model, entry.usage) for entry in response.usage_by_model] == [
            (Model.FAKE.value, Usage(input_tokens=10, output_tokens=5)),
            (Model.FAKE_SMALL.value, Usage(input_tokens=100, output_tokens=50)),
        ]
        # The sum equals the response usage — nothing billed goes missing.
        assert response.usage == Usage(input_tokens=110, output_tokens=55)

    async def test_blocking_failure_carries_billed_usage(self) -> None:
        """A mid-loop blocking failure reports what was already billed —
        the pre-N0 blocking path raised with usage=None."""
        fake = FakeClient(
            FakeScript(
                turns=(
                    _greet_script().turns[0],
                    FakeTurn(error=TimeoutError("main died mid-loop")),
                )
            )
        )
        agent = Agent(_config(tools=[_greet], client_factory=lambda _: fake))
        with pytest.raises(ModelFailedError) as exc_info:
            await agent.run(_USER, stream=False)
        assert exc_info.value.usage == Usage(input_tokens=10, output_tokens=5)
        assert exc_info.value.usage_by_model == (
            ModelUsage(
                model=Model.FAKE.value, usage=Usage(input_tokens=10, output_tokens=5)
            ),
        )

    async def test_exhausted_fallback_carries_both_attempts_usage(self) -> None:
        fake = FakeClient(
            FakeScript(
                turns=(
                    _greet_script().turns[0],
                    FakeTurn(error=TimeoutError("main died")),
                    FakeTurn(error=TimeoutError("fallback died")),
                )
            )
        )
        agent = Agent(
            _config(
                tools=[_greet],
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda _: fake,
            )
        )
        from neosian._foundation.shared.exceptions import FallbackExhaustedError

        with pytest.raises(FallbackExhaustedError) as exc_info:
            await agent.run(_USER, stream=False)
        # Only the main attempt billed anything; it still reaches the error.
        assert exc_info.value.usage == Usage(input_tokens=10, output_tokens=5)
        assert [entry.model for entry in exc_info.value.usage_by_model] == [
            Model.FAKE.value
        ]


def _recording_hooks(events: list[object]) -> AgentHooks:
    return AgentHooks(
        on_turn=events.append,
        on_llm_call=events.append,
        on_tool=events.append,
        on_fallback=events.append,
        strict=True,
    )


@pytest.mark.unit
class TestHookIntegration:
    """The four hooks fired by real Agent code paths, keyless."""

    async def test_blocking_tool_loop_sequence(self) -> None:
        events: list[object] = []
        fake = FakeClient(_greet_script())
        agent = Agent(
            _config(
                tools=[_greet],
                client_factory=lambda _: fake,
                hooks=_recording_hooks(events),
            )
        )
        response = await agent.run(_USER, stream=False)

        assert [type(e).__name__ for e in events] == [
            "LlmCallEvent",
            "ToolEvent",
            "LlmCallEvent",
            "TurnEvent",
        ]
        first_call = events[0]
        assert isinstance(first_call, LlmCallEvent)
        assert first_call.requested_model == Model.FAKE.value
        assert first_call.model == Model.FAKE.value
        assert first_call.iteration == 0
        assert first_call.streamed is False
        assert first_call.usage == Usage(input_tokens=10, output_tokens=5)
        assert first_call.error_code is None

        tool = events[1]
        assert isinstance(tool, ToolEvent)
        assert tool.name == "greet"
        assert tool.result.data == "Hello ada"
        assert tool.iteration == 0

        turn = events[3]
        assert isinstance(turn, TurnEvent)
        assert turn.streamed is False
        assert turn.response is response  # the exact returned object

    async def test_streaming_sequence_matches_blocking(self) -> None:
        blocking_events: list[object] = []
        agent = Agent(
            _config(
                tools=[_greet],
                client_factory=lambda _: FakeClient(_greet_script()),
                hooks=_recording_hooks(blocking_events),
            )
        )
        await agent.run(_USER, stream=False)

        streaming_events: list[object] = []
        agent = Agent(
            _config(
                tools=[_greet],
                client_factory=lambda _: FakeClient(_greet_script()),
                hooks=_recording_hooks(streaming_events),
            )
        )
        async for _ in await agent.run(_USER, stream=True):
            pass

        assert [type(e).__name__ for e in streaming_events] == [
            type(e).__name__ for e in blocking_events
        ]

    async def test_streamed_turn_event_carries_turn_messages(self) -> None:
        """Streaming turn capture is observable through on_turn — the
        synthesized response satisfies the DESIGN §3 contract."""
        events: list[object] = []
        agent = Agent(
            _config(
                tools=[_greet],
                client_factory=lambda _: FakeClient(_greet_script()),
                hooks=_recording_hooks(events),
            )
        )
        async for _ in await agent.run(_USER, stream=True):
            pass

        turn = events[-1]
        assert isinstance(turn, TurnEvent)
        assert turn.streamed is True
        response = turn.response
        assert response.message.content == "Greeted ada"
        assert [m.role for m in response.turn_messages] == [
            Role.ASSISTANT,
            Role.TOOL,
            Role.ASSISTANT,
        ]
        assert response.turn_messages[-1] is response.message
        assert response.usage == Usage(input_tokens=40, output_tokens=9)
        assert [tc.name for tc in response.tool_calls_made] == ["greet"]
        assert response.model == Model.FAKE.value

    async def test_fallback_event_fields(self) -> None:
        events: list[object] = []
        fake = FakeClient(
            FakeScript(
                turns=(
                    FakeTurn(error=TimeoutError("main model down")),
                    FakeTurn(content="recovered"),
                )
            )
        )
        agent = Agent(
            _config(
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda _: fake,
                hooks=_recording_hooks(events),
            )
        )
        await agent.run(_USER, stream=False)

        fallback = next(e for e in events if isinstance(e, FallbackEvent))
        assert fallback.from_model == Model.FAKE.value
        assert fallback.to_model == Model.FAKE_SMALL.value
        assert fallback.sticky is False
        assert fallback.streamed is False
        # The fake raises through wrap_provider_error, so the cause carries
        # the machine code — structure, not formatted English.
        assert fallback.cause_code == "llm_provider_error"
        assert "main model down" in fallback.reason

    async def test_failed_call_still_fires_llm_call(self) -> None:
        events: list[object] = []
        fake = FakeClient(FakeScript(turns=(FakeTurn(error=TimeoutError("down")),)))
        agent = Agent(
            _config(client_factory=lambda _: fake, hooks=_recording_hooks(events))
        )
        with pytest.raises(ModelFailedError):
            await agent.run(_USER, stream=False)

        assert len(events) == 1  # the failed call; no turn event on a raise
        failed = events[0]
        assert isinstance(failed, LlmCallEvent)
        assert failed.error_code == "llm_provider_error"
        assert failed.model is None

    async def test_run_and_session_hook_sequences_identical(self) -> None:
        """DESIGN §3: both entry points produce identical hook sequences."""

        def signature(events: list[object]) -> list[tuple[str, object]]:
            keyed: list[tuple[str, object]] = []
            for event in events:
                if isinstance(event, LlmCallEvent):
                    keyed.append(("llm_call", event.iteration))
                elif isinstance(event, ToolEvent):
                    keyed.append(("tool", event.call_id))
                elif isinstance(event, TurnEvent):
                    keyed.append(("turn", event.streamed))
                elif isinstance(event, FallbackEvent):
                    keyed.append(("fallback", event.sticky))
            return keyed

        direct_events: list[object] = []
        agent = Agent(
            _config(
                tools=[_greet],
                client_factory=lambda _: FakeClient(_greet_script()),
                hooks=_recording_hooks(direct_events),
            )
        )
        await agent.run(_USER, stream=False)

        session_events: list[object] = []
        agent = Agent(
            _config(
                tools=[_greet],
                client_factory=lambda _: FakeClient(_greet_script()),
                hooks=_recording_hooks(session_events),
            )
        )
        async with agent.session() as session:
            await session.run(_USER, stream=False)

        assert signature(direct_events) == signature(session_events)


@pytest.mark.unit
class TestStreamingErrorUsage:
    """Billed usage reaches terminal errors on the streaming path — via the
    attempt ledger now, not the retired exception-attribute smuggle."""

    async def test_midstream_failure_carries_partial_usage(self) -> None:
        """A stream that dies after the Anthropic-shape partial usage chunk
        still reports those billed input/cache tokens."""
        from neosian._foundation.llm.fake import StreamShape

        script = FakeScript(
            turns=(
                FakeTurn(
                    content="hi",
                    usage=Usage(input_tokens=9, output_tokens=4, cache_read_tokens=2),
                    error=ConnectionError("mid-stream drop"),
                    error_after_chunks=2,  # lead partial + one content chunk
                ),
            ),
            stream_shape=StreamShape.ANTHROPIC,
        )
        agent = Agent(_config(client_factory=lambda _: FakeClient(script)))
        with pytest.raises(ModelFailedError) as exc_info:
            async for _ in await agent.run(_USER, stream=True):
                pass
        assert exc_info.value.usage == Usage(
            input_tokens=9, output_tokens=0, cache_read_tokens=2
        )
        assert [entry.model for entry in exc_info.value.usage_by_model] == [
            Model.FAKE.value
        ]

    async def test_streamed_done_event_includes_failed_main_billing(self) -> None:
        """After a billed-then-failed main attempt, the fallback's done event
        reports everything the run spent — main's partial included."""
        from neosian._foundation.llm.fake import StreamShape

        script = FakeScript(
            turns=(
                FakeTurn(
                    content="hi",
                    usage=Usage(input_tokens=9, output_tokens=4),
                    error=ConnectionError("main died"),
                    error_after_chunks=1,  # bills the lead partial (9 input)
                ),
                FakeTurn(
                    content="recovered",
                    usage=Usage(input_tokens=100, output_tokens=50),
                ),
            ),
            stream_shape=StreamShape.ANTHROPIC,
        )
        fake = FakeClient(script)
        agent = Agent(
            _config(
                fallback=FallbackConfig(model=Model.FAKE_SMALL),
                client_factory=lambda _: fake,
            )
        )
        events = [event async for event in await agent.run(_USER, stream=True)]
        done = next(e for e in events if isinstance(e, DoneEvent))
        assert done.usage is not None
        assert done.usage.input_tokens == 109
        assert done.usage.output_tokens == 50
        # The per-model split survives onto the wire payload too.
        payload = done.to_dict()
        assert [entry["model"] for entry in payload["usage_by_model"]] == [
            Model.FAKE.value,
            Model.FAKE_SMALL.value,
        ]


@pytest.mark.unit
class TestTwinCollapse:
    """The N0 session-twin collapse: one code path, two entry points."""

    def test_no_session_twin_methods_survive(self) -> None:
        """The *_with_session / *_session method twins stay dead."""
        stale = [
            name for name in dir(Agent) if name.endswith(("_with_session", "_session"))
        ]
        assert stale == []

    async def test_run_and_session_run_are_identical(self) -> None:
        """One script through both entry points: same response, same calls."""

        @Tool(name="greet", description="Say hello")
        async def greet(name: str) -> ToolResult[str]:
            return ToolResult.ok(f"Hello {name}")

        script = FakeScript(
            turns=(
                FakeTurn(
                    tool_calls=(
                        ToolCall(
                            id=ToolCallId("c1"),
                            name=ToolName("greet"),
                            arguments={"name": "ada"},
                        ),
                    ),
                    usage=Usage(input_tokens=10, output_tokens=5),
                ),
                FakeTurn(
                    content="Greeted ada",
                    usage=Usage(input_tokens=30, output_tokens=4),
                ),
            )
        )

        direct_fake = FakeClient(script)
        direct_agent = Agent(
            _config(tools=[greet], client_factory=lambda _: direct_fake)
        )
        direct = await direct_agent.run(_USER, stream=False)

        session_fake = FakeClient(script)
        session_agent = Agent(
            _config(tools=[greet], client_factory=lambda _: session_fake)
        )
        async with session_agent.session() as session:
            sessioned = await session.run(_USER, stream=False)

        assert direct == sessioned
        assert direct_fake.calls == session_fake.calls
