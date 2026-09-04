"""Evaluation config vocabulary (DESIGN §13).

Schema v2: one agent under test, a variants × models × cases matrix,
strict keys, typed matchers. Every type is frozen — the loader builds
them once and nothing downstream mutates them.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Final

from neosian._foundation.llm.fake import FakeTurn
from neosian._foundation.shared.types import AnyModel, ToolName

if TYPE_CHECKING:
    from neosian._foundation.evaluation.memory_types import MemoryEvalConfig


class EvalKind(str, Enum):
    """Discriminator for eval config shapes (`kind:` in YAML)."""

    AGENT = "agent"
    MEMORY = "memory"


class MatchMode(str, Enum):
    """How a ValueMatcher compares an expected value to an actual one."""

    EQUALS = "equals"
    CONTAINS = "contains"
    REGEX = "regex"
    EXISTS = "exists"


@dataclass(frozen=True, slots=True)
class ValueMatcher:
    """One typed comparison.

    `value` is mode-shaped: EQUALS holds any YAML value, CONTAINS a str,
    REGEX a compiled `re.Pattern[str]` (compiled at load — a bad pattern
    fails the config, never a run), EXISTS holds None.
    """

    mode: MatchMode
    value: Any = None

    def describe(self) -> str:
        """Render for failure messages and the JSON artifact."""
        if self.mode is MatchMode.EXISTS:
            return "exists"
        if self.mode is MatchMode.REGEX:
            return f"regex {self.value.pattern!r}"
        return f"{self.mode.value} {self.value!r}"


@dataclass(frozen=True, slots=True)
class SequenceStep:
    """One step of an exact tool-call sequence expectation."""

    tool: ToolName
    params: Mapping[str, ValueMatcher] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Expectation:
    """What one turn must exhibit. The loader guarantees it is non-empty
    and internally consistent (`tool`/`sequence`/`no_tool` mutually
    exclusive; `params` only beside `tool`); `response` composes with any.
    """

    tool: ToolName | None = None
    params: Mapping[str, ValueMatcher] = field(default_factory=dict)
    sequence: tuple[SequenceStep, ...] | None = None
    no_tool: bool = False
    response: tuple[ValueMatcher, ...] = ()


@dataclass(frozen=True, slots=True)
class EvalTurn:
    """One user turn and its expectation.

    `tool_results` sets what stubbed tools return *during this turn* —
    the payload is in place before the model call, so the recorded
    history is exactly what the model saw.
    """

    user: str
    expect: Expectation
    tool_results: Mapping[ToolName, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvalCase:
    """One case. A one-shot `input:` is sugar for a single turn.

    `script` runs the case keylessly on a scripted FakeClient (one
    instance per case — the cursor survives multi-call tool rounds).
    `execute_tools` really runs the named user tools instead of stubbing.
    """

    name: str
    turns: tuple[EvalTurn, ...]
    script: tuple[FakeTurn, ...] | None = None
    execute_tools: frozenset[ToolName] = frozenset()

    @property
    def is_conversational(self) -> bool:
        """More than one turn."""
        return len(self.turns) > 1

    @property
    def total_turns(self) -> int:
        """Number of turns this case evaluates."""
        return len(self.turns)


@dataclass(frozen=True, slots=True)
class Variant:
    """One prompting strategy: system prompt + tool-description overrides.

    A suite without `variants:` runs the single implicit BASE_VARIANT —
    the agent's own prompt and descriptions, untouched.
    """

    name: str
    system_prompt: str | None = None
    tool_descriptions: Mapping[ToolName, str] = field(default_factory=dict)
    source: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Variant name must be non-empty")


BASE_VARIANT: Final = Variant(name="base")


@dataclass(frozen=True, slots=True)
class AgentEvalConfig:
    """A `kind: agent` suite: variants × models × cases over one agent.

    `execute_tools` is the suite-wide allowlist (cases may extend it);
    `ignore_tools` hides names from matchers while still capturing them —
    the escape hatch for always-executing builtins like `update_todo`.
    """

    name: str
    agent: str
    models: tuple[AnyModel, ...]
    cases: tuple[EvalCase, ...]
    variants: tuple[Variant, ...] = (BASE_VARIANT,)
    execute_tools: frozenset[ToolName] = frozenset()
    ignore_tools: frozenset[ToolName] = frozenset()
    stop_on_failure: bool = True
    throttle_ms: int = 500

    kind: ClassVar[EvalKind] = EvalKind.AGENT

    @property
    def variant_names(self) -> tuple[str, ...]:
        return tuple(v.name for v in self.variants)

    @property
    def model_names(self) -> tuple[str, ...]:
        return tuple(m.value for m in self.models)

    @property
    def case_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.cases)


# The discriminated union the runner dispatches on. Every member carries
# the three axis-name properties — the config-side twin of EvalReport's
# own axes, so presentation never needs the concrete type.
type EvalConfig = AgentEvalConfig | MemoryEvalConfig
