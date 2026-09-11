"""The eval harness's errors (DESIGN §13.10)."""

from __future__ import annotations

from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.exceptions.base import NeosianError


# Evaluation Errors
class EvalError(NeosianError):
    """Base exception for evaluation-related errors."""

    code = "eval_error"


class EvalConfigNotFoundError(EvalError):
    """Raised when an eval config file is not found."""

    code = "eval_config_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorMessages.EVAL_CONFIG_NOT_FOUND.format(path=path),
            details={"path": path},
        )
        self.path = path


class EvalConfigInvalidYAMLError(EvalError):
    """Raised when an eval config file is unparseable or structurally
    invalid (non-mapping root, duplicate variant names, …)."""

    code = "eval_config_invalid_yaml"

    def __init__(self, path: str, detail: str | None = None) -> None:
        message = ErrorMessages.EVAL_CONFIG_INVALID_YAML.format(path=path)
        if detail is not None:
            message = f"{message} — {detail}"
        super().__init__(message, details={"path": path, "detail": detail})
        self.path = path
        self.detail = detail


class EvalConfigMissingKeyError(EvalError):
    """Raised when a required key is missing from eval config."""

    code = "eval_config_missing_key"

    def __init__(self, key: str, path: str) -> None:
        super().__init__(
            ErrorMessages.EVAL_CONFIG_MISSING_KEY.format(key=key, path=path),
            details={"key": key, "path": path},
        )
        self.key = key
        self.path = path


class EvalPromptNotFoundError(EvalError):
    """Raised when a prompt file referenced in eval config is not found."""

    code = "eval_prompt_not_found"

    def __init__(self, path: str) -> None:
        super().__init__(
            ErrorMessages.EVAL_PROMPT_NOT_FOUND.format(path=path),
            details={"path": path},
        )
        self.path = path


class EvalCaseInvalidError(EvalError):
    """Raised when an eval case definition is invalid."""

    code = "eval_case_invalid"

    def __init__(self, name: str, error: str) -> None:
        super().__init__(
            ErrorMessages.EVAL_CASE_INVALID.format(name=name, error=error),
            details={"name": name, "error": error},
        )
        self.name = name
        self.error = error


class EvalRunError(EvalError):
    """Raised when a case fails at the harness level (agent load, script
    exhaustion) rather than on an expectation."""

    code = "eval_run_failed"

    def __init__(self, variant: str, model: str, case: str, error: str) -> None:
        super().__init__(
            ErrorMessages.EVAL_RUN_ERROR.format(
                variant=variant, model=model, case=case, error=error
            ),
            details={"variant": variant, "model": model, "case": case, "error": error},
        )
        self.variant = variant
        self.model = model
        self.case = case
        self.error = error


class EvalConfigUnknownKeyError(EvalError):
    """Raised when an eval config carries a key the schema does not know."""

    code = "eval_config_unknown_key"

    def __init__(self, key: str, path: str, hint: str | None = None) -> None:
        message = f"Unknown key '{key}' in eval config: {path}"
        if hint is not None:
            message = f"{message} — {hint}"
        super().__init__(message, details={"key": key, "path": path, "hint": hint})
        self.key = key
        self.path = path
        self.hint = hint


class EvalModelUnknownError(EvalError):
    """Raised when an eval config's models axis names an unknown model."""

    code = "eval_model_unknown"

    def __init__(self, model: str, path: str) -> None:
        super().__init__(
            f"Unknown model '{model}' in eval config: {path}",
            details={"model": model, "path": path},
        )
        self.model = model
        self.path = path
