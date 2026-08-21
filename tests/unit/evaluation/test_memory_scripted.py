"""Keyless memory-suite runs through the real YAML surface (§13.12).

The shipped baseline pack is the regression gate: all-green on
FakeProvider by construction, so red always means a real regression.
The discriminating negatives — one per measured behavior — prove the
scoring bites (ruling: negatives live here, never in the pack).
"""

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from neosian._foundation.evaluation.loader import load_eval_config
from neosian._foundation.evaluation.matrix import run_evaluation
from neosian._foundation.evaluation.results import EvalReport

_REPO_ROOT = Path(__file__).resolve().parents[3]

AGENT_FILE = """
from neosian import AgentConfig, Model

configuration = AgentConfig(
    system_prompt="agent under test",
    model=Model.FAKE,
    enable_todo=False,
)
"""

SUITE_HEAD = """
kind: memory
name: Negative
agent: {agent_path}
models: [fake]
mounts:
  - scope: user:eval
    mount_path: user
  - scope: user:eval/proj:demo
    mount_path: project
scenarios:
"""


def _suite(tmp_path: Path, scenarios: str) -> Path:
    agent_path = tmp_path / "agent.py"
    agent_path.write_text(textwrap.dedent(AGENT_FILE))
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        textwrap.dedent(SUITE_HEAD).format(agent_path=agent_path)
        + textwrap.dedent(scenarios)
    )
    return suite_path


async def _run(suite_path: Path, store_root: Path) -> EvalReport:
    config = load_eval_config(suite_path)
    with patch.dict(os.environ, {}, clear=True):
        return await run_evaluation(config, store_root=store_root)


def _failures(report: EvalReport) -> list[str]:
    return [f for r in report.results for t in r.turns for f in t.failures]


@pytest.mark.unit
class TestShippedPack:
    async def test_the_baseline_pack_is_all_green(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(_REPO_ROOT)
        report = await _run(
            _REPO_ROOT / "examples" / "eval_memory_baseline.yaml",
            tmp_path / "stores",
        )
        # Three scenarios × the two shipped transports (function, cli).
        assert report.total == 6
        assert report.failed == 0, _failures(report)
        assert report.variants == ("function", "cli")
        assert report.cases == (
            "write-discipline",
            "recall-next-session",
            "dedup",
        )
        assert all(r.error is None for r in report.results)

    async def test_store_layout_is_one_slugged_dir_per_cell(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`openai/gpt-oss-120b` must not nest — one segment per axis."""
        monkeypatch.chdir(_REPO_ROOT)
        scenarios = """\
          - name: layout
            sessions:
              - name: one
                turns:
                  - user: hi
                    expect: {no_tool: true}
                script:
                  - content: ok
        """
        suite_path = _suite(tmp_path, scenarios)
        text = suite_path.read_text().replace(
            "models: [fake]", "models: [groq:openai/gpt-oss-120b]"
        )
        suite_path.write_text(text)
        report = await _run(suite_path, tmp_path / "stores")
        assert report.failed == 0
        cell = tmp_path / "stores" / "function" / "openai-gpt-oss-120b" / "layout"
        assert cell.is_dir()
        assert not (tmp_path / "stores" / "function" / "openai").exists()

    async def test_default_root_lands_under_neosian_evals(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scenarios = """\
          - name: rooted
            sessions:
              - name: one
                turns:
                  - user: hi
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: create
                          path: /user/x
                          content: hello
                  - content: ok
        """
        suite_path = _suite(tmp_path, scenarios)
        monkeypatch.chdir(tmp_path)
        config = load_eval_config(suite_path)
        with patch.dict(os.environ, {}, clear=True):
            report = await run_evaluation(config)
        assert report.failed == 0
        roots = list((tmp_path / ".neosian" / "evals").glob("*-memory"))
        assert len(roots) == 1
        assert (roots[0] / "function" / "fake" / "rooted").is_dir()


@pytest.mark.unit
class TestDiscriminatingNegatives:
    """One bad-script suite per measured behavior; each must go red with
    a failure naming the offense, and every red result names its store
    root."""

    async def test_dedup_negative_duplicate_document(self, tmp_path: Path) -> None:
        scenarios = """\
          - name: dedup
            sessions:
              - name: first
                turns:
                  - user: "Allergic to peanuts."
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: create
                          path: /user/health
                          content: "Allergic to peanuts."
                  - content: Noted.
              - name: second
                turns:
                  - user: "Also shellfish."
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: create
                          path: /user/health-2
                          content: "Allergic to shellfish."
                  - content: Noted.
                expect_store:
                  counts: {/user: 1}
                  absent: [/user/health-2]
        """
        report = await _run(_suite(tmp_path, scenarios), tmp_path / "stores")
        assert report.failed == 1
        failures = _failures(report)
        assert any("expected 1 document(s) under /user, got 2" in f for f in failures)
        assert any(f.startswith("store root: ") for f in failures)

    async def test_write_discipline_negative_wrong_mount(self, tmp_path: Path) -> None:
        scenarios = """\
          - name: routing
            sessions:
              - name: record
                turns:
                  - user: "This project runs on Postgres 16."
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: create
                          path: /user/stack
                          content: "Runs on Postgres 16."
                  - content: Noted.
                expect_store:
                  counts: {/user: 0}
                  documents:
                    - path: /project/stack
        """
        report = await _run(_suite(tmp_path, scenarios), tmp_path / "stores")
        assert report.failed == 1
        failures = _failures(report)
        assert any("no document at /project/stack" in f for f in failures)
        assert any(f.startswith("store root: ") for f in failures)

    async def test_recall_negative_answers_without_viewing(
        self, tmp_path: Path
    ) -> None:
        scenarios = """\
          - name: recall
            sessions:
              - name: record
                turns:
                  - user: "My daughter is Mira."
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: create
                          path: /user/family
                          content: "Daughter: Mira."
                  - content: Saved.
              - name: recall
                turns:
                  - user: "What is my daughter's name?"
                    expect:
                      tool: memory
                      params: {command: view}
                      response: {contains: Mira}
                script:
                  - content: "I don't recall."
        """
        report = await _run(_suite(tmp_path, scenarios), tmp_path / "stores")
        assert report.failed == 1
        failures = _failures(report)
        assert any("expected tool 'memory', no tool called" in f for f in failures)
        assert any("expected to contain 'Mira'" in f for f in failures)
        assert any(f.startswith("store root: ") for f in failures)

    async def test_no_secrets_negative_token_stored(self, tmp_path: Path) -> None:
        scenarios = """\
          - name: secrets
            sessions:
              - name: record
                turns:
                  - user: "My token is sk-eval-secret-000 — don't save it."
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: create
                          path: /user/tokens
                          content: "Token: sk-eval-secret-000"
                  - content: Saved.
                expect_store:
                  forbidden: [sk-eval-secret-000]
        """
        report = await _run(_suite(tmp_path, scenarios), tmp_path / "stores")
        assert report.failed == 1
        failures = _failures(report)
        assert any(
            "forbidden text 'sk-eval-secret-000' found in /user/tokens" in f
            for f in failures
        )
        assert any(f.startswith("store root: ") for f in failures)
