"""Suite loading — YAML schema v2, strict keys, loud failures (DESIGN §13)."""

import textwrap
from pathlib import Path

import pytest

from neosian._foundation.evaluation.loader import load_eval_config
from neosian._foundation.evaluation.types import (
    BASE_VARIANT,
    AgentEvalConfig,
    EvalKind,
    MatchMode,
)
from neosian._foundation.shared.exceptions import (
    EvalCaseInvalidError,
    EvalConfigInvalidYAMLError,
    EvalConfigMissingKeyError,
    EvalConfigNotFoundError,
    EvalConfigUnknownKeyError,
    EvalModelUnknownError,
)
from neosian._foundation.shared.types import Model

MINIMAL = """
name: suite
agent: agent.py
models: [fake]
cases:
  - name: c
    input: hi
    expect: {no_tool: true}
"""


def _write(tmp_path: Path, body: str, filename: str = "suite.yaml") -> Path:
    path = tmp_path / filename
    path.write_text(textwrap.dedent(body))
    return path


def _load_agent(tmp_path: Path, body: str) -> AgentEvalConfig:
    config = load_eval_config(_write(tmp_path, body))
    assert isinstance(config, AgentEvalConfig)
    return config


@pytest.mark.unit
class TestSuiteLevel:
    def test_minimal_suite_parses_with_defaults(self, tmp_path: Path) -> None:
        config = _load_agent(tmp_path, MINIMAL)
        assert config.kind is EvalKind.AGENT
        assert config.name == "suite"
        assert config.agent == "agent.py"
        assert config.models == (Model.FAKE,)
        assert config.variants == (BASE_VARIANT,)
        assert config.execute_tools == frozenset()
        assert config.ignore_tools == frozenset()
        assert config.stop_on_failure is True
        assert config.throttle_ms == 500
        assert len(config.cases) == 1

    def test_explicit_kind_agent_is_accepted(self, tmp_path: Path) -> None:
        config = load_eval_config(_write(tmp_path, "kind: agent" + MINIMAL))
        assert config.kind is EvalKind.AGENT

    def test_unknown_kind_fails(self, tmp_path: Path) -> None:
        with pytest.raises(
            EvalConfigInvalidYAMLError,
            match="unknown kind 'judge' — known kinds: agent, memory",
        ):
            load_eval_config(_write(tmp_path, "kind: judge" + MINIMAL))

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(EvalConfigNotFoundError):
            load_eval_config(tmp_path / "absent.yaml")

    def test_unparseable_yaml(self, tmp_path: Path) -> None:
        with pytest.raises(EvalConfigInvalidYAMLError):
            load_eval_config(_write(tmp_path, "name: [unclosed"))

    def test_non_mapping_root(self, tmp_path: Path) -> None:
        with pytest.raises(EvalConfigInvalidYAMLError):
            load_eval_config(_write(tmp_path, "- just\n- a list\n"))

    @pytest.mark.parametrize("key", ["name", "agent", "models", "cases"])
    def test_missing_required_key(self, tmp_path: Path, key: str) -> None:
        body = textwrap.dedent(MINIMAL).split("cases:")[0] + (
            "cases: [{name: c, input: hi, expect: {no_tool: true}}]\n"
        )
        body = "\n".join(line for line in body.splitlines() if not line.startswith(key))
        with pytest.raises(EvalConfigMissingKeyError) as excinfo:
            load_eval_config(_write(tmp_path, body))
        assert excinfo.value.key == key

    def test_unknown_top_level_key(self, tmp_path: Path) -> None:
        with pytest.raises(EvalConfigUnknownKeyError) as excinfo:
            load_eval_config(_write(tmp_path, MINIMAL + "verbose: true\n"))
        assert excinfo.value.key == "verbose"

    def test_v1_prompts_key_gets_the_migration_hint(self, tmp_path: Path) -> None:
        with pytest.raises(EvalConfigUnknownKeyError) as excinfo:
            load_eval_config(_write(tmp_path, MINIMAL + "prompts: [a.py]\n"))
        assert excinfo.value.key == "prompts"
        assert "'agent:' + 'variants:'" in str(excinfo.value)

    def test_options_parse(self, tmp_path: Path) -> None:
        body = MINIMAL + (
            "stop_on_failure: false\nthrottle_ms: 0\n"
            "execute_tools: [write]\nignore_tools: [update_todo]\n"
        )
        config = load_eval_config(_write(tmp_path, body))
        assert config.stop_on_failure is False
        assert config.throttle_ms == 0
        assert config.execute_tools == frozenset({"write"})
        assert config.ignore_tools == frozenset({"update_todo"})

    @pytest.mark.parametrize(
        "line",
        [
            "stop_on_failure: maybe",
            "throttle_ms: fast",
            "throttle_ms: -1",
            "throttle_ms: true",
            "execute_tools: nope",
            "ignore_tools: [1]",
        ],
    )
    def test_malformed_options_fail(self, tmp_path: Path, line: str) -> None:
        with pytest.raises(EvalConfigInvalidYAMLError):
            load_eval_config(_write(tmp_path, MINIMAL + line + "\n"))


@pytest.mark.unit
class TestModelsAxis:
    def test_provider_prefixed_and_bare_forms(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "models: [fake]",
            "models: [groq:openai/gpt-oss-20b, openai/gpt-oss-120b, fake-small]",
        )
        config = load_eval_config(_write(tmp_path, body))
        assert config.models == (
            Model.GROQ_GPT_OSS_20B,
            Model.GROQ_GPT_OSS_120B,
            Model.FAKE_SMALL,
        )

    def test_unknown_model_fails_at_load(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("models: [fake]", "models: [gpt-99]")
        with pytest.raises(EvalModelUnknownError) as excinfo:
            load_eval_config(_write(tmp_path, body))
        assert excinfo.value.model == "gpt-99"

    def test_empty_models_fail(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("models: [fake]", "models: []")
        with pytest.raises(EvalConfigInvalidYAMLError):
            load_eval_config(_write(tmp_path, body))


@pytest.mark.unit
class TestVariantsAxis:
    def _prompt_file(self, tmp_path: Path, name: str) -> Path:
        return _write(tmp_path, f"system_prompt: You are {name}.\n", f"{name}.yaml")

    def test_variants_load_their_prompt_files(self, tmp_path: Path) -> None:
        a = self._prompt_file(tmp_path, "a")
        b = self._prompt_file(tmp_path, "b")
        body = (
            MINIMAL
            + f"variants:\n  - {{name: a, prompt: {a}}}\n  - {{name: b, prompt: {b}}}\n"
        )
        config = _load_agent(tmp_path, body)
        assert [v.name for v in config.variants] == ["a", "b"]
        assert config.variants[0].system_prompt == "You are a."
        assert config.variants[0].source == str(a)

    def test_duplicate_variant_names_fail(self, tmp_path: Path) -> None:
        a = self._prompt_file(tmp_path, "a")
        body = (
            MINIMAL
            + f"variants:\n  - {{name: a, prompt: {a}}}\n  - {{name: a, prompt: {a}}}\n"
        )
        with pytest.raises(EvalConfigInvalidYAMLError, match="duplicate variant"):
            load_eval_config(_write(tmp_path, body))

    def test_variant_entry_missing_prompt_fails(self, tmp_path: Path) -> None:
        body = MINIMAL + "variants:\n  - {name: a}\n"
        with pytest.raises(EvalConfigMissingKeyError):
            load_eval_config(_write(tmp_path, body))

    def test_variant_entry_unknown_key_fails(self, tmp_path: Path) -> None:
        a = self._prompt_file(tmp_path, "a")
        body = MINIMAL + f"variants:\n  - {{name: a, prompt: {a}, weight: 2}}\n"
        with pytest.raises(EvalConfigUnknownKeyError):
            load_eval_config(_write(tmp_path, body))


CASE_TABLE = [
    ("  - input: hi\n    expect: {no_tool: true}", "missing 'name'"),
    ("  - name: c", "must have 'input' or 'conversation'"),
    (
        "  - name: c\n    input: hi\n    conversation: []\n    expect: {no_tool: true}",
        "'input' and 'conversation' are exclusive",
    ),
    ("  - name: c\n    input: hi", "requires 'expect'"),
    ("  - name: c\n    input: hi\n    expect: {}", "non-empty mapping"),
    (
        "  - name: c\n    input: hi\n    expect: {no_tool: true}\n    retries: 3",
        "unknown key 'retries'",
    ),
    (
        "  - name: c\n    expect: {no_tool: true}\n    conversation:\n"
        "      - user: hi\n        expect: {no_tool: true}",
        "put it on the turn",
    ),
    (
        "  - name: c\n    conversation:\n      - expect: {no_tool: true}",
        "turn 1 missing 'user'",
    ),
    (
        "  - name: c\n    conversation:\n      - user: hi",
        "turn 1 missing 'expect'",
    ),
    (
        "  - name: c\n    conversation:\n"
        "      - user: hi\n        expect: {no_tool: true}\n"
        "        mock_response: {ok: true}",
        "schema v2 replaced it with 'tool_results'",
    ),
    (
        "  - name: c\n    conversation:\n"
        "      - user: hi\n        expect: {no_tool: true}\n"
        "        tool_results: nope",
        "'tool_results' must be a mapping",
    ),
]


@pytest.mark.unit
class TestCases:
    def test_one_shot_is_a_single_turn(self, tmp_path: Path) -> None:
        config = _load_agent(tmp_path, MINIMAL)
        case = config.cases[0]
        assert case.is_conversational is False
        assert case.total_turns == 1
        assert case.turns[0].user == "hi"
        assert case.turns[0].expect.no_tool is True

    def test_conversational_case_with_tool_results(self, tmp_path: Path) -> None:
        body = """
        name: suite
        agent: agent.py
        models: [fake]
        cases:
          - name: conv
            execute_tools: [write]
            conversation:
              - user: "draw a sunset"
                expect:
                  tool: draw
                  params: {prompt: _exists}
                tool_results:
                  draw: {url: "https://x/y.png"}
              - user: "now a video"
                expect:
                  tool: animate
                  params: {url: {equals: "https://x/y.png"}}
        """
        case = _load_agent(tmp_path, body).cases[0]
        assert case.is_conversational is True
        assert case.execute_tools == frozenset({"write"})
        assert case.turns[0].tool_results == {"draw": {"url": "https://x/y.png"}}
        assert case.turns[0].expect.params["prompt"].mode is MatchMode.EXISTS
        assert case.turns[1].expect.params["url"].mode is MatchMode.EQUALS

    @pytest.mark.parametrize(("case_yaml", "message"), CASE_TABLE)
    def test_invalid_cases_fail_loudly(
        self, tmp_path: Path, case_yaml: str, message: str
    ) -> None:
        body = "name: s\nagent: a.py\nmodels: [fake]\ncases:\n" + case_yaml + "\n"
        with pytest.raises(EvalCaseInvalidError, match=message):
            load_eval_config(_write(tmp_path, body))

    def test_duplicate_case_names_fail(self, tmp_path: Path) -> None:
        body = MINIMAL + "  - name: c\n    input: yo\n    expect: {no_tool: true}\n"
        with pytest.raises(EvalConfigInvalidYAMLError, match="duplicate case"):
            load_eval_config(_write(tmp_path, body))
