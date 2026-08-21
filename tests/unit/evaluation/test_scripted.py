"""Scripted eval runs — the harness exercised keylessly, end to end.

A case carrying `script:` runs against a scripted FakeClient injected via
client_factory: no API keys, no network, expectations still scored. The
suite here goes through the real YAML surface, exactly as a user would.
"""

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from neosian._foundation.evaluation.loader import load_eval_config
from neosian._foundation.evaluation.matrix import run_evaluation
from neosian._foundation.evaluation.types import AgentEvalConfig
from neosian._foundation.llm.base import ToolCall
from neosian._foundation.llm.fake import FakeTurn
from neosian._foundation.shared.exceptions import EvalCaseInvalidError
from neosian._foundation.shared.types import ToolCallId, ToolName

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
agent: {agent_path}
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
    expect:
      response: {{contains: "hi"}}
    script:
      - content: "hi"
"""


def _suite(tmp_path: Path) -> Path:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text(textwrap.dedent(AGENT_FILE))
    config_path = tmp_path / "eval.yaml"
    config_path.write_text(EVAL_YAML.format(agent_path=agent_path))
    return config_path


@pytest.mark.unit
class TestScriptParsing:
    def test_script_parses_into_fake_turns(self, tmp_path: Path) -> None:
        config = load_eval_config(_suite(tmp_path))
        assert isinstance(config, AgentEvalConfig)

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
            "name: bad\nagent: a.py\nmodels: [fake]\ncases:\n"
            "  - name: c\n    input: hi\n    expect: {no_tool: true}\n"
            f"    {script_yaml}\n"
        )
        with pytest.raises(EvalCaseInvalidError):
            load_eval_config(config_path)


@pytest.mark.unit
class TestKeylessScriptedRun:
    async def test_run_evaluation_end_to_end_with_zero_keys(
        self, tmp_path: Path
    ) -> None:
        config = load_eval_config(_suite(tmp_path))
        with patch.dict(os.environ, {}, clear=True):
            report = await run_evaluation(config)

        assert report.total == 2
        assert report.passed == 2
        tool_case = report.result_for("base", "fake", "tool-case")
        assert tool_case is not None
        assert tool_case.tool_sequence == ("lookup",)
        assert tool_case.error is None
