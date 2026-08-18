"""Runner fallback detection on the typed hook seam — keyless.

The pre-N0 detector scraped log substrings off a hard-coded module path;
these are the runner's first tests, made possible by on_fallback +
FakeProvider.
"""

import pytest

from neosian._foundation.agent.base import Agent
from neosian._foundation.agent.hooks import AgentHooks, FallbackEvent
from neosian._foundation.evaluation.runner import _FallbackRecorder, _run_one_shot
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.shared.types import (
    AgentConfig,
    EvalCase,
    FallbackConfig,
    Model,
    SystemPrompt,
)


def _agent(recorder: _FallbackRecorder, script: FakeScript) -> Agent:
    fake = FakeClient(script)
    return Agent(
        AgentConfig(
            system_prompt=SystemPrompt("You are a test agent."),
            model=Model.FAKE,
            enable_todo=False,
            fallback=FallbackConfig(model=Model.FAKE_SMALL),
            client_factory=lambda _: fake,
            hooks=AgentHooks(on_fallback=recorder),
        )
    )


@pytest.mark.unit
class TestRunnerFallbackDetection:
    async def test_one_shot_fails_case_on_fallback(self) -> None:
        recorder = _FallbackRecorder()
        agent = _agent(
            recorder,
            FakeScript(
                turns=(
                    FakeTurn(error=TimeoutError("main down")),
                    FakeTurn(content="recovered"),
                )
            ),
        )
        case = EvalCase(name="fallback-case", input="Hi")
        result = await _run_one_shot("prompt.py", "fake", case, agent, recorder)
        assert result.passed is False
        assert result.error is not None
        assert Model.FAKE_SMALL.value in result.error

    async def test_clean_run_records_nothing(self) -> None:
        recorder = _FallbackRecorder()
        agent = _agent(recorder, FakeScript(turns=(FakeTurn(content="clean"),)))
        case = EvalCase(name="clean-case", input="Hi")
        result = await _run_one_shot("prompt.py", "fake", case, agent, recorder)
        assert recorder.event is None
        assert result.error is None

    def test_sticky_retry_main_is_not_a_failure(self) -> None:
        recorder = _FallbackRecorder()
        recorder(
            FallbackEvent(
                from_model="fake-small",
                to_model="fake",
                reason="retry_main_after threshold reached",
                cause_code=None,
                provider_status=None,
                sticky=True,
                streamed=False,
            )
        )
        assert recorder.event is None
        assert recorder.reason is None
