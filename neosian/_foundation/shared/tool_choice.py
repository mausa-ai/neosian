"""How a call must treat its tools (DESIGN §3, NC9 ledger #224).

Its own module, re-exported by `shared.types`, for the reason
`guardrail_types` and `context_policy` are: a value type with its own
validation reads better beside itself than inside the 400-line type
sheet.

The four shapes are every wire's four shapes. Build one with the
constructors rather than the field pair: the constructors are the
contract, the fields are what the converters read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ToolChoiceMode = Literal["auto", "required", "none", "tool"]

_NAME_REQUIRED = 'ToolChoice.tool() needs the tool\'s name; got ""'
_NAME_UNEXPECTED = "ToolChoice.{mode}() takes no tool name; got {name!r}"


@dataclass(frozen=True)
class ToolChoice:
    """Whether the model may, must, or must not call a tool this turn.

    Attributes:
        mode: Which of the four shapes this is.
        name: The tool `mode="tool"` forces; None for every other mode.
        parallel: False asks the wire for at most one call per turn. Only
            Anthropic has a knob for it (`disable_parallel_tool_use`) and
            the OpenAI wire a body flag; a door that takes neither simply
            answers as it would have.

    Example:
        from neosian import ToolChoice

        await agent.run(
            messages,
            stream=False,
            tools=["search"],
            tool_choice=ToolChoice.required(),
        )
    """

    mode: ToolChoiceMode = "auto"
    name: str | None = None
    parallel: bool = True

    def __post_init__(self) -> None:
        # Lazy import to avoid the circular dependency at module load
        # time (the idiom AgentConfig.__post_init__ uses).
        from neosian._foundation.shared.exceptions import UnsupportedParameterError

        if self.mode == "tool" and not self.name:
            raise UnsupportedParameterError(_NAME_REQUIRED)
        if self.mode != "tool" and self.name is not None:
            raise UnsupportedParameterError(
                _NAME_UNEXPECTED.format(mode=self.mode, name=self.name)
            )

    @classmethod
    def auto(cls, *, parallel: bool = True) -> ToolChoice:
        """The provider's default: the model answers or calls, as it judges."""
        return cls(mode="auto", parallel=parallel)

    @classmethod
    def required(cls, *, parallel: bool = True) -> ToolChoice:
        """The model must call a tool this turn; which one is its choice."""
        return cls(mode="required", parallel=parallel)

    @classmethod
    def none(cls) -> ToolChoice:
        """No tool is called: the model answers in text, tools still in view."""
        return cls(mode="none")

    @classmethod
    def tool(cls, name: str, *, parallel: bool = True) -> ToolChoice:
        """The model must call exactly `name`."""
        return cls(mode="tool", name=name, parallel=parallel)

    @property
    def forces_a_call(self) -> bool:
        """True when the model cannot answer in text this turn."""
        return self.mode in ("required", "tool")
