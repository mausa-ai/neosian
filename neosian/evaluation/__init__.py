"""Public evaluation surface (DESIGN §13). Import as `neosian.evaluation`.

Re-exports only — the implementation lives in _foundation.evaluation.
The root package never imports this module; it loads only when you do.
Evaluation is a dev-time harness, so nothing here rides the root
`__all__` (the `neosian.fake` precedent); `EvalError` alone stays root,
with the rest of the exception family here.
"""

from neosian._foundation.evaluation.loader import load_eval_config
from neosian._foundation.evaluation.matrix import run_evaluation
from neosian._foundation.evaluation.memory_types import (
    DocumentExpectation,
    MemoryEvalConfig,
    MemoryScenario,
    MemorySession,
    SeedDocument,
    StoreExpectation,
    Transport,
)
from neosian._foundation.evaluation.progress import (
    EvalProgress,
    create_progress_callback,
)
from neosian._foundation.evaluation.reporter import print_report, save_report
from neosian._foundation.evaluation.results import (
    CaseResult,
    CaseStatus,
    EvalReport,
    ProgressCallback,
    ProgressEvent,
    ToolCallCapture,
    TurnResult,
)
from neosian._foundation.evaluation.types import (
    BASE_VARIANT,
    AgentEvalConfig,
    EvalCase,
    EvalConfig,
    EvalKind,
    EvalTurn,
    Expectation,
    MatchMode,
    SequenceStep,
    ValueMatcher,
    Variant,
)
from neosian._foundation.shared.exceptions import (
    EvalCaseInvalidError,
    EvalConfigInvalidYAMLError,
    EvalConfigMissingKeyError,
    EvalConfigNotFoundError,
    EvalConfigUnknownKeyError,
    EvalError,
    EvalModelUnknownError,
    EvalPromptNotFoundError,
    EvalRunError,
)

__all__ = [
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
