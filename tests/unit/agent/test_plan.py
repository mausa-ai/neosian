"""The fallback plan: one policy, two drivers (DESIGN §3, NC7).

The plan's decisions are pinned as a table; the sticky-session walk runs
the same four-run story through both drivers on FakeProvider, so the
streaming driver's sticky branches are covered exactly as the blocking
driver's are.
"""

from __future__ import annotations

import pytest

from neosian import (
    Agent,
    AgentConfig,
    AgentHooks,
    FallbackConfig,
    FallbackEvent,
    Model,
)
from neosian._foundation.agent.context import RunContext
from neosian._foundation.agent.plan import (
    Leg,
    LegOutcome,
    Plan,
    build_plan,
    record_success,
)
from neosian._foundation.agent.session import AgentSession
from neosian._foundation.llm.base import ImageBlock, Message, Role
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.exceptions import (
    FallbackExhaustedError,
    ModelFailedError,
)
from neosian._foundation.shared.types import FallbackState

_USER = [Message(role=Role.USER, content="Hi")]
_IMAGE = [
    Message(
        role=Role.USER,
        content=[ImageBlock(media_type="image/png", data="aGk=")],
    )
]
_MAIN = Leg(Model.FAKE, LegOutcome.MAIN_OK)


def _agent(
    fake: FakeClient | None = None,
    *,
    fallback: bool = True,
    retry_main_after: int = 0,
    events: list[object] | None = None,
) -> Agent:
    return Agent(
        AgentConfig(
            system_prompt="You are a test agent.",
            model=Model.FAKE,
            enable_todo=False,
            fallback=(
                FallbackConfig(
                    model=Model.FAKE_SMALL, retry_main_after=retry_main_after
                )
                if fallback
                else None
            ),
            client_factory=(lambda _: fake) if fake is not None else None,
            hooks=AgentHooks(on_fallback=events.append) if events is not None else None,
        )
    )


def _ctx(agent: Agent, state: FallbackState | None) -> RunContext:
    return RunContext(
        agent=agent,
        acquire=agent._create_client,
        hooks=agent._hooks,
        fallback_state=state,
    )


@pytest.mark.unit
class TestBuildPlan:
    def test_no_fallback_is_one_leg(self) -> None:
        assert build_plan(_ctx(_agent(fallback=False), None), _USER) == Plan(
            legs=(_MAIN,)
        )

    def test_main_then_fallback(self) -> None:
        plan = build_plan(_ctx(_agent(), FallbackState()), _USER)
        assert plan == Plan(legs=(_MAIN, Leg(Model.FAKE_SMALL, LegOutcome.FALLBACK_OK)))

    def test_sticky_tries_the_fallback_first_then_main(self) -> None:
        state = FallbackState(using_fallback=True, successful_fallback_calls=1)
        plan = build_plan(_ctx(_agent(), state), _USER)
        assert plan == Plan(
            legs=(Leg(Model.FAKE_SMALL, LegOutcome.FALLBACK_AGAIN), _MAIN)
        )

    def test_sticky_media_routes_to_main_alone(self) -> None:
        """FAKE_SMALL carries no media: the sticky session goes straight to main."""
        state = FallbackState(using_fallback=True, successful_fallback_calls=1)
        assert build_plan(_ctx(_agent(), state), _IMAGE) == Plan(legs=(_MAIN,))

    def test_retry_main_threshold_flips_state_and_emits_the_return(self) -> None:
        state = FallbackState(using_fallback=True, successful_fallback_calls=2)
        plan = build_plan(_ctx(_agent(retry_main_after=2), state), _USER)
        assert plan.legs == (_MAIN, Leg(Model.FAKE_SMALL, LegOutcome.FALLBACK_OK))
        assert plan.preamble is not None
        assert (plan.preamble.from_model, plan.preamble.to_model) == (
            "fake-small",
            "fake",
        )
        assert plan.preamble.sticky is True and plan.preamble.cause is None
        assert state == FallbackState()

    def test_sticky_without_a_fallback_is_a_contract_breach(self) -> None:
        state = FallbackState(using_fallback=True)
        with pytest.raises(ModelFailedError, match="using_fallback=True"):
            build_plan(_ctx(_agent(fallback=False), state), _USER)


@pytest.mark.unit
class TestRecordSuccess:
    def test_outside_a_session_nothing_is_recorded(self) -> None:
        record_success(_MAIN, None)

    @pytest.mark.parametrize(
        ("outcome", "expected"),
        [
            (LegOutcome.MAIN_OK, FallbackState()),
            (LegOutcome.FALLBACK_OK, FallbackState(True, 1)),
            (LegOutcome.FALLBACK_AGAIN, FallbackState(True, 4)),
        ],
    )
    def test_each_outcome_moves_the_state(
        self, outcome: LegOutcome, expected: FallbackState
    ) -> None:
        state = FallbackState(using_fallback=True, successful_fallback_calls=3)
        record_success(Leg(Model.FAKE_SMALL, outcome), state)
        assert state == expected


def _script(*turns: FakeTurn) -> FakeScript:
    return FakeScript(turns=turns)


def _down(who: str) -> FakeTurn:
    return FakeTurn(error=TimeoutError(f"{who} down"))


@pytest.mark.unit
@pytest.mark.parametrize("stream", [False, True], ids=["blocking", "streaming"])
class TestStickySession:
    """The same walk through both drivers; the script plays turns in order,
    whichever model asks."""

    async def _run(self, session: AgentSession, stream: bool) -> None:
        if stream:
            async for _ in await session.run(_USER, stream=True):
                pass
        else:
            await session.run(_USER, stream=False)

    async def test_the_four_run_walk(self, stream: bool) -> None:
        events: list[object] = []
        fake = FakeClient(
            _script(
                _down("main"),  # run 1: main fails
                FakeTurn(content="fallback"),  # run 1: fallback answers
                FakeTurn(content="fallback"),  # run 2: sticky fallback again
                _down("fallback"),  # run 3: sticky fallback fails
                FakeTurn(content="main"),  # run 3: main recovers
                _down("main"),  # run 4: main fails
                _down("fallback"),  # run 4: fallback fails
            )
        )
        agent = _agent(fake, events=events)
        async with agent.session() as session:
            state = session._fallback_state
            await self._run(session, stream)
            assert state == FallbackState(True, 1)
            await self._run(session, stream)
            assert state == FallbackState(True, 2)
            await self._run(session, stream)
            assert state == FallbackState()
            with pytest.raises(FallbackExhaustedError) as info:
                await self._run(session, stream)
        assert "main down" in info.value.main_error
        assert "fallback down" in info.value.fallback_error

        switches = [
            (e.from_model, e.to_model, e.sticky, e.streamed)
            for e in events
            if isinstance(e, FallbackEvent)
        ]
        assert switches == [
            ("fake", "fake-small", False, stream),
            ("fake-small", "fake", False, stream),
            ("fake", "fake-small", False, stream),
        ]

    async def test_sticky_exhaustion_names_each_model_own_error(
        self, stream: bool
    ) -> None:
        fake = FakeClient(_script(_down("fallback"), _down("main")))
        async with _agent(fake).session() as session:
            session._fallback_state.using_fallback = True
            session._fallback_state.successful_fallback_calls = 1
            with pytest.raises(FallbackExhaustedError) as info:
                await self._run(session, stream)
        assert "main down" in info.value.main_error
        assert "fallback down" in info.value.fallback_error

    async def test_retry_main_return_is_a_sticky_event(self, stream: bool) -> None:
        events: list[object] = []
        fake = FakeClient(
            _script(
                _down("main"),
                FakeTurn(content="fallback"),  # run 1 → calls = 1
                FakeTurn(content="fallback"),  # run 2 → calls = 2, threshold
                FakeTurn(content="main"),  # run 3: the return to main
            )
        )
        agent = _agent(fake, retry_main_after=2, events=events)
        async with agent.session() as session:
            for _ in range(3):
                await self._run(session, stream)
            assert session._fallback_state == FallbackState()
        last = events[-1]
        assert isinstance(last, FallbackEvent)
        assert (last.from_model, last.to_model) == ("fake-small", "fake")
        assert last.sticky is True and last.cause_code is None
        assert last.streamed is stream
