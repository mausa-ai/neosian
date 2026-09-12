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
    UnsupportedParameterError,
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
        assert plan == Plan(
            legs=(_MAIN, Leg(Model.FAKE_SMALL, LegOutcome.FALLBACK_OK, index=1))
        )

    def test_sticky_tries_the_fallback_first_then_main(self) -> None:
        state = FallbackState(using_fallback=True, successful_fallback_calls=1)
        plan = build_plan(_ctx(_agent(), state), _USER)
        assert plan == Plan(
            legs=(Leg(Model.FAKE_SMALL, LegOutcome.FALLBACK_AGAIN, index=1), _MAIN)
        )

    def test_sticky_media_routes_to_main_alone(self) -> None:
        """FAKE_SMALL carries no media: the sticky session goes straight to main."""
        state = FallbackState(using_fallback=True, successful_fallback_calls=1)
        assert build_plan(_ctx(_agent(), state), _IMAGE) == Plan(legs=(_MAIN,))

    def test_retry_main_threshold_flips_state_and_emits_the_return(self) -> None:
        state = FallbackState(using_fallback=True, successful_fallback_calls=2)
        plan = build_plan(_ctx(_agent(retry_main_after=2), state), _USER)
        assert plan.legs == (
            _MAIN,
            Leg(Model.FAKE_SMALL, LegOutcome.FALLBACK_OK, index=1),
        )
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


def _ladder_agent(
    fake: FakeClient | None = None,
    *,
    rungs: tuple[Model, ...] = (Model.FAKE_REASONING, Model.FAKE_SMALL),
    retry_main_after: int = 0,
    events: list[object] | None = None,
) -> Agent:
    """An agent whose fallback is a ladder rather than a single rung."""
    return Agent(
        AgentConfig(
            system_prompt="You are a test agent.",
            model=Model.FAKE,
            enable_todo=False,
            fallback=FallbackConfig(
                models=list(rungs), retry_main_after=retry_main_after
            ),
            client_factory=(lambda _: fake) if fake is not None else None,
            hooks=AgentHooks(on_fallback=events.append) if events is not None else None,
        )
    )


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


@pytest.mark.unit
class TestTheLadder:
    """A fallback of more than one rung (NC9, ledger #223). NC7 built the
    plan so this is a change to `legs` and nothing else."""

    _R1 = Leg(Model.FAKE_REASONING, LegOutcome.FALLBACK_OK, index=1)
    _R2 = Leg(Model.FAKE_SMALL, LegOutcome.FALLBACK_OK, index=2)

    def test_the_ladder_is_walked_top_down(self) -> None:
        plan = build_plan(_ctx(_ladder_agent(), FallbackState()), _USER)
        assert plan == Plan(legs=(_MAIN, self._R1, self._R2))

    def test_one_rung_reads_exactly_as_it_always_did(self) -> None:
        """models=[X] and model=X are the same plan — the regression pin."""
        ladder = build_plan(
            _ctx(_ladder_agent(rungs=(Model.FAKE_SMALL,)), FallbackState()), _USER
        )
        single = build_plan(_ctx(_agent(), FallbackState()), _USER)
        assert ladder == single

    def test_a_sticky_rung_walks_down_before_main(self) -> None:
        """The rung that stuck, then the rungs below it, then main last."""
        state = FallbackState(using_fallback=True, fallback_index=0)
        plan = build_plan(_ctx(_ladder_agent(), state), _USER)
        assert plan == Plan(
            legs=(
                Leg(Model.FAKE_REASONING, LegOutcome.FALLBACK_AGAIN, index=1),
                self._R2,
                _MAIN,
            )
        )

    def test_a_sticky_lower_rung_starts_from_itself(self) -> None:
        state = FallbackState(using_fallback=True, fallback_index=1)
        plan = build_plan(_ctx(_ladder_agent(), state), _USER)
        assert plan == Plan(
            legs=(
                Leg(Model.FAKE_SMALL, LegOutcome.FALLBACK_AGAIN, index=2),
                _MAIN,
            )
        )

    def test_a_rung_that_cannot_carry_the_content_is_dropped(self) -> None:
        """Media is never downgraded: the rung leaves the ladder rather
        than being attempted (DESIGN §2). FAKE_REASONING has no images."""
        plan = build_plan(_ctx(_ladder_agent(), FallbackState()), _IMAGE)
        assert plan == Plan(legs=(_MAIN,))

    def test_record_success_follows_the_rung_that_answered(self) -> None:
        state = FallbackState()
        record_success(self._R2, state)
        assert state == FallbackState(
            using_fallback=True, successful_fallback_calls=1, fallback_index=1
        )
        record_success(_MAIN, state)
        assert state == FallbackState()

    async def test_every_rung_is_tried_once_in_order(self) -> None:
        events: list[object] = []
        fake = FakeClient(
            _script(_down("main"), _down("reasoning"), FakeTurn(content="small"))
        )
        agent = _ladder_agent(fake, events=events)
        response = await agent.run(_USER, stream=False)
        assert response.message.content == "small"
        assert [call.model for call in fake.calls] == [
            Model.FAKE,
            Model.FAKE_REASONING,
            Model.FAKE_SMALL,
        ]
        assert [
            (e.from_model, e.to_model) for e in events if isinstance(e, FallbackEvent)
        ] == [("fake", "fake-reasoning"), ("fake-reasoning", "fake-small")]

    @pytest.mark.parametrize("stream", [False, True], ids=["blocking", "streaming"])
    async def test_a_whole_ladder_failing_names_every_attempt(
        self, stream: bool
    ) -> None:
        fake = FakeClient(_script(_down("main"), _down("reasoning"), _down("small")))
        agent = _ladder_agent(fake)
        with pytest.raises(FallbackExhaustedError) as info:
            if stream:
                async for _ in await agent.run(_USER, stream=True):
                    pass
            else:
                await agent.run(_USER, stream=False)
        error = info.value
        assert [model for model, _ in error.attempts] == [
            "fake",
            "fake-reasoning",
            "fake-small",
        ]
        # main_/fallback_ keep meaning the main model and a rung, never
        # "first and last tried".
        assert error.main_model == "fake" and "main down" in error.main_error
        assert error.fallback_model == "fake-small"
        assert "small down" in error.fallback_error
        assert "All 3 models" in str(error)

    async def test_two_rungs_keep_the_original_message(self) -> None:
        """A two-leg exhaustion reads exactly as it did before the ladder."""
        fake = FakeClient(_script(_down("main"), _down("fallback")))
        with pytest.raises(FallbackExhaustedError) as info:
            await _agent(fake).run(_USER, stream=False)
        assert "Both main model (fake) and fallback model (fake-small) failed" in str(
            info.value
        )
        assert len(info.value.attempts) == 2


@pytest.mark.unit
class TestLadderConfig:
    def test_model_and_models_are_mutually_exclusive(self) -> None:
        with pytest.raises(UnsupportedParameterError, match="never both"):
            FallbackConfig(model=Model.FAKE, models=[Model.FAKE_SMALL])

    def test_a_fallback_needs_at_least_one_rung(self) -> None:
        with pytest.raises(UnsupportedParameterError, match="both were empty"):
            FallbackConfig()

    def test_model_fills_the_ladder(self) -> None:
        assert FallbackConfig(model=Model.FAKE_SMALL).models == (Model.FAKE_SMALL,)

    def test_models_fills_model_with_its_first_rung(self) -> None:
        config = FallbackConfig(models=[Model.FAKE_SMALL, Model.FAKE_REASONING])
        assert config.model is Model.FAKE_SMALL
