"""Scripted eval runs — the harness exercised keylessly (N0 slice B).

A case carrying `script:` runs against a scripted FakeClient injected via
client_factory: no API keys, no network, tool expectations still scored.
"""

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from neosian._foundation.evaluation.loader import load_eval_config
from neosian._foundation.evaluation.runner import _needs_throttle, run_evaluation
from neosian._foundation.llm.base import ToolCall
from neosian._foundation.llm.fake import FakeTurn
from neosian._foundation.shared.exceptions import EvalCaseInvalidError
from neosian._foundation.shared.types import EvalCase, ToolCallId, ToolName

AGENT_FILE = """
from neosian import AgentConfig, Tool, ToolResult


@Tool(name="lookup", description="Look something up")
async def lookup(q: str) -> ToolResult[str]:
    return ToolResult.ok("real")


configuration = AgentConfig(
    system_prompt="You are a test agent.",
    tools=[lookup],
    enable_todo=False,
)
"""

EVAL_YAML = """
name: scripted-suite
prompts:
  - {agent_path}
models:
  - fake
cases:
  - name: tool-case
    input: "Find x"
    expect:
      tool: lookup
      params:
        q: x
    script:
      - tool_calls:
          - name: lookup
            arguments:
              q: x
      - content: "Found it."
  - name: text-case
    input: "Say hi"
    script:
      - content: "hi"
"""


@pytest.mark.unit
class TestScriptParsing:
    def test_script_parses_into_fake_turns(self, tmp_path: Path) -> None:
        agent_path = tmp_path / "agent.py"
        agent_path.write_text(textwrap.dedent(AGENT_FILE))
        config_path = tmp_path / "eval.yaml"
        config_path.write_text(EVAL_YAML.format(agent_path=agent_path))

        config = load_eval_config(config_path)

        assert config.cases[0].script == (
            FakeTurn(
                tool_calls=(
                    ToolCall(
                        id=ToolCallId("script_0_0"),
                        name=ToolName("lookup"),
                        arguments={"q": "x"},
                    ),
                )
            ),
            FakeTurn(content="Found it."),
        )
        assert config.cases[1].script == (FakeTurn(content="hi"),)

    @pytest.mark.parametrize(
        "script_yaml",
        [
            "script: {}",  # not a list
            "script: []",  # empty
            "script:\n      - 5",  # turn not a mapping
            "script:\n      - tool_calls:\n          - arguments: {}",  # no name
        ],
    )
    def test_malformed_script_raises(self, tmp_path: Path, script_yaml: str) -> None:
        config_path = tmp_path / "eval.yaml"
        config_path.write_text(
            "name: bad\nprompts: [a.py]\nmodels: [fake]\ncases:\n"
            f"  - name: c\n    input: hi\n    {script_yaml}\n"
        )
        with pytest.raises(EvalCaseInvalidError):
            load_eval_config(config_path)


@pytest.mark.unit
class TestKeylessScriptedRun:
    async def test_run_evaluation_end_to_end_with_zero_keys(
        self, tmp_path: Path
    ) -> None:
        agent_path = tmp_path / "agent.py"
        agent_path.write_text(textwrap.dedent(AGENT_FILE))
        config_path = tmp_path / "eval.yaml"
        config_path.write_text(EVAL_YAML.format(agent_path=agent_path))

        config = load_eval_config(config_path)
        with patch.dict(os.environ, {}, clear=True):
            results = await run_evaluation(config)

        assert [r.passed for r in results] == [True, True]
        assert results[0].tool_sequence == ["lookup"]
        assert results[0].error is None


@pytest.mark.unit
class TestThrottleExemption:
    def test_scripted_and_fake_runs_skip_the_throttle(self) -> None:
        scripted = EvalCase(name="s", input="x", script=(FakeTurn(content="y"),))
        plain = EvalCase(name="p", input="x")
        assert _needs_throttle("fake", plain) is False
        assert _needs_throttle("groq:openai/gpt-oss-20b", scripted) is False
        assert _needs_throttle("not-a-model", plain) is False
        assert _needs_throttle("openai/gpt-oss-20b", plain) is True
