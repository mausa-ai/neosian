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
        # Ten scenarios × the three shipped transports (function, cli,
        # http — the NM wire, ledger #113).
        assert report.total == 30
        assert report.failed == 0, _failures(report)
        assert report.variants == ("function", "cli", "http")
        assert report.cases == (
            "write-discipline",
            "recall-next-session",
            "dedup",
            "contradiction",
            "long-horizon-recall",
            "correct-wrong-memory",
            "reflection-close",
            "maintenance",
            "cross-client",
            "skills",
        )
        assert all(r.error is None for r in report.results)

    async def test_store_layout_is_one_slugged_dir_per_cell(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`gpt-oss-120b` must not nest — one segment per axis."""
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
            "models: [fake]", "models: [cerebras:gpt-oss-120b]"
        )
        suite_path.write_text(text)
        report = await _run(suite_path, tmp_path / "stores")
        assert report.failed == 0
        cell = tmp_path / "stores" / "function" / "gpt-oss-120b" / "layout"
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

    async def test_contradiction_negative_stale_fact_survives(
        self, tmp_path: Path
    ) -> None:
        """A second document beside the stale one: the reversal must
        erase Paris from every live document, not merely outnumber it."""
        scenarios = """\
          - name: contradiction
            sessions:
              - name: record
                turns:
                  - user: "I live in Paris."
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: create
                          path: /user/location
                          content: "Lives in Paris."
                  - content: Noted.
              - name: contradict
                turns:
                  - user: "Actually I've moved — Lisbon now."
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: create
                          path: /user/location-new
                          content: "Lives in Lisbon."
                  - content: Updated.
                expect_store:
                  counts: {/user: 1}
                  forbidden: [Paris]
        """
        report = await _run(_suite(tmp_path, scenarios), tmp_path / "stores")
        assert report.failed == 1
        failures = _failures(report)
        assert any("expected 1 document(s) under /user, got 2" in f for f in failures)
        assert any(
            "forbidden text 'Paris' found in /user/location" in f for f in failures
        )
        assert any(f.startswith("store root: ") for f in failures)

    async def test_long_horizon_negative_rewrites_instead_of_recalling(
        self, tmp_path: Path
    ) -> None:
        """The final session re-creates the fact instead of viewing it —
        the untouched-store pin (versions) must go red. The turn
        expectation stays loose: a failed turn stops the session before
        store scoring, and the view-vs-write turn catch already lives in
        test_recall_negative_answers_without_viewing."""
        scenarios = """\
          - name: long-horizon
            sessions:
              - name: record
                turns:
                  - user: "My cat is called Biscuit."
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: create
                          path: /user/pets
                          content: "Cat: Biscuit."
                  - content: Saved.
              - name: recall
                turns:
                  - user: "What is my cat's name?"
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: create
                          path: /user/pets
                          content: "Cat: Biscuit."
                  - content: "Your cat is Biscuit."
                expect_store:
                  counts: {/user: 1}
                  documents:
                    - path: /user/pets
                      versions: 1
        """
        report = await _run(_suite(tmp_path, scenarios), tmp_path / "stores")
        assert report.failed == 1
        failures = _failures(report)
        assert any(
            "expected 1 version rows, got 2 (actions: created, modified)" in f
            for f in failures
        )
        assert any(f.startswith("store root: ") for f in failures)

    async def test_correction_negative_softened_edit_keeps_the_claim(
        self, tmp_path: Path
    ) -> None:
        """Disavowal answered with a softened edit instead of delete —
        the document survives and the claim stays in live text."""
        scenarios = """\
          - name: correction
            sessions:
              - name: record
                turns:
                  - user: "I'm planning to switch this project to MongoDB."
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: create
                          path: /project/database-plan
                          content: "Plans to switch to MongoDB."
                  - content: Noted.
              - name: disavow
                turns:
                  - user: "That MongoDB note is wrong — remove it."
                    expect: {tool: memory}
                script:
                  - tool_calls:
                      - name: memory
                        arguments:
                          command: str_replace
                          path: /project/database-plan
                          old_str: "Plans to switch to MongoDB."
                          new_str: "(withdrawn) Plans to switch to MongoDB."
                  - content: Softened.
                expect_store:
                  counts: {/project: 0}
                  absent: [/project/database-plan]
                  forbidden: [MongoDB]
        """
        report = await _run(_suite(tmp_path, scenarios), tmp_path / "stores")
        assert report.failed == 1
        failures = _failures(report)
        assert any(
            "expected no document at /project/database-plan" in f for f in failures
        )
        assert any(
            "forbidden text 'MongoDB' found in /project/database-plan" in f
            for f in failures
        )
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

    async def test_reflection_negative_duplicate_instead_of_update(
        self, tmp_path: Path
    ) -> None:
        """A reflection pass that creates a near-duplicate beside the
        document it was shown must fail the count."""
        scenarios = """\
          - name: reflect-dedup
            sessions:
              - name: chat
                reflect: true
                turns:
                  - user: "I only drink espresso."
                    expect: {no_tool: true}
                script:
                  - content: Understood.
                  - content: '{"ops": [{"command": "create",
                      "path": "/user/preferences",
                      "content": "Drinks espresso only."}]}'
              - name: revise
                reflect: true
                turns:
                  - user: "Make that ristretto."
                    expect: {no_tool: true}
                script:
                  - content: Sure.
                  - content: '{"ops": [{"command": "create",
                      "path": "/user/preferences-2",
                      "content": "Drinks ristretto only."}]}'
                expect_store:
                  counts: {/user: 1}
        """
        report = await _run(_suite(tmp_path, scenarios), tmp_path / "stores")
        assert report.failed == 1
        failures = _failures(report)
        assert any("expected 1 document(s) under /user, got 2" in f for f in failures)
        assert any(f.startswith("store root: ") for f in failures)

    async def test_reflection_negative_token_stored(self, tmp_path: Path) -> None:
        """A reflection pass that distills the refused token into the
        store must fail the forbidden check."""
        scenarios = """\
          - name: reflect-secrets
            sessions:
              - name: chat
                reflect: true
                turns:
                  - user: "My token is sk-eval-secret-000 — don't save it."
                    expect: {no_tool: true}
                script:
                  - content: Understood.
                  - content: '{"ops": [{"command": "create",
                      "path": "/user/tokens",
                      "content": "Token: sk-eval-secret-000"}]}'
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

    async def test_maintenance_negative_semantic_duplicates_survive(
        self, tmp_path: Path
    ) -> None:
        """Two seeded documents carry the same fact in different bytes —
        below the deterministic stage's reach. A gardener that declines
        the merge leaves two live documents; the count red is the
        measurement."""
        scenarios = """\
          - name: gardening
            seed:
              - path: /user/coffee
                content: "Drinks espresso only."
              - path: /user/drinks
                content: "Prefers espresso, nothing else."
            sessions:
              - name: garden
                maintain: true
                script:
                  - content: '{"ops": []}'
                expect_store:
                  counts: {/user: 1}
        """
        report = await _run(_suite(tmp_path, scenarios), tmp_path / "stores")
        assert report.failed == 1
        failures = _failures(report)
        assert any("expected 1 document(s) under /user, got 2" in f for f in failures)
        assert any(f.startswith("store root: ") for f in failures)

    async def test_maintenance_negative_secret_written(self, tmp_path: Path) -> None:
        """A gardener batch that writes a secret into the store must
        fail the forbidden check — the no-secrets rule binds every
        engine, not only the turn loop."""
        scenarios = """\
          - name: gardening-secrets
            seed:
              - path: /user/coffee
                content: "Drinks espresso only."
            sessions:
              - name: garden
                maintain: true
                script:
                  - content: '{"ops": [{"command": "create",
                      "path": "/user/tokens",
                      "content": "Token: sk-eval-secret-000"}]}'
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


_CROSS_CLIENT_HEAD = """\
  - name: cross
    sessions:
      - name: writes
        record:
          agent: claude-code
          session_id: cc-1
          prompt: "Add a retry."
          stop: "Added a retry with backoff."
      - name: reads
        session_start: true
        turns:
          - user: what changed?
            expect:
              tool: recall_turn
              params: {conversation: cc-1, turn: 1}
        script:
"""


@pytest.mark.unit
class TestCrossClientNegatives:
    """The switching claim's scoring bites (§21.7): the wrong turn and a
    reading session that writes are both red."""

    async def test_recalling_the_wrong_turn_is_red(self, tmp_path: Path) -> None:
        scenarios = _CROSS_CLIENT_HEAD + """\
          - tool_calls:
              - name: recall_turn
                arguments: {turn: 2, conversation: cc-1}
          - content: nothing
        """
        report = await _run(_suite(tmp_path, scenarios), tmp_path / "stores")
        assert report.failed == 1
        assert any("param 'turn'" in f for f in _failures(report))

    async def test_a_reading_session_that_writes_is_red(self, tmp_path: Path) -> None:
        scenarios = _CROSS_CLIENT_HEAD + """\
          - tool_calls:
              - name: recall_turn
                arguments: {turn: 1, conversation: cc-1}
          - tool_calls:
              - name: memory
                arguments: {command: create, path: /project/note, content: x}
          - content: done
        expect_store:
          counts: {/project: 1}
        """
        report = await _run(_suite(tmp_path, scenarios), tmp_path / "stores")
        assert report.failed == 1
        assert any("/project" in f and "2" in f for f in _failures(report))
