"""The neosian.evaluation facade: pinned surface, lazily loaded (DESIGN §13)."""

import subprocess
import sys

import pytest


@pytest.mark.unit
def test_evaluation_all_is_pinned() -> None:
    import neosian.evaluation

    assert neosian.evaluation.__all__ == [
        "AgentEvalConfig",
        "BASE_VARIANT",
        "CaseResult",
        "CaseStatus",
        "DocumentExpectation",
        "EvalCase",
        "EvalCaseInvalidError",
        "EvalConfig",
        "EvalConfigInvalidYAMLError",
        "EvalConfigMissingKeyError",
        "EvalConfigNotFoundError",
        "EvalConfigUnknownKeyError",
        "EvalError",
        "EvalKind",
        "EvalModelUnknownError",
        "EvalProgress",
        "EvalPromptNotFoundError",
        "EvalReport",
        "EvalRunError",
        "EvalTurn",
        "Expectation",
        "MatchMode",
        "MemoryEvalConfig",
        "MemoryScenario",
        "MemorySession",
        "ProgressCallback",
        "ProgressEvent",
        "SeedDocument",
        "SequenceStep",
        "StoreExpectation",
        "ToolCallCapture",
        "Transport",
        "TurnResult",
        "ValueMatcher",
        "Variant",
        "create_progress_callback",
        "load_eval_config",
        "print_report",
        "run_evaluation",
        "save_report",
    ]
    for name in neosian.evaluation.__all__:
        assert getattr(neosian.evaluation, name) is not None


@pytest.mark.unit
def test_root_import_does_not_load_evaluation_facade() -> None:
    """`import neosian` must not pull in neosian.evaluation (lazy, dev-time)."""
    code = "import neosian, sys; assert 'neosian.evaluation' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)
