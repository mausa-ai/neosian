"""Validation of the new effort levels shared by configs and adapters."""

from neosian._foundation.shared.exceptions import UnsupportedParameterError
from neosian._foundation.shared.models import ModelSpec, ReasoningEffort


def validate_effort(spec: ModelSpec, effort: ReasoningEffort | None) -> None:
    """Refuse new levels instead of silently weakening an explicit ask.

    Existing MAX downgrades remain the provider adapters' contract.
    """
    if effort is None:
        return
    if not isinstance(effort, ReasoningEffort):
        raise UnsupportedParameterError("reasoning_effort must be a ReasoningEffort")
    allowed = {
        ReasoningEffort.XHIGH: spec.supports_xhigh_effort,
        ReasoningEffort.NONE: spec.supports_no_effort,
    }
    if effort in allowed and (
        not spec.supports_reasoning
        or not allowed[effort]
        or (spec.door is not None and not spec.door.reasoning_effort)
    ):
        raise UnsupportedParameterError(
            f"reasoning_effort={effort.value!r} is not supported by this model"
        )
