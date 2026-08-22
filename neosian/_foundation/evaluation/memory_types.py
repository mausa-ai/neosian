"""Memory-eval config vocabulary (DESIGN §13.12).

A `kind: memory` suite measures the library's own memory layer —
write discipline, recall-in-next-session, dedup — as a transports ×
models × scenarios matrix. A scenario is an ordered list of sessions,
each a fresh bare Agent over the same per-cell store root; turn
expectations stay loose, store truth carries the strictness.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

from neosian._foundation.evaluation.types import (
    EvalKind,
    EvalTurn,
    ValueMatcher,
)
from neosian._foundation.llm.fake import FakeTurn
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.memory.types import MemoryAction
from neosian._foundation.shared.types import Model, ToolName


class Transport(str, Enum):
    """How the memory tool reaches the wire: the plain function tool;
    Anthropic's native `memory_20250818` declaration (ledger #43 — the
    marker degrades to the function schema off Anthropic, so that axis
    is informative only on Anthropic runs); or the shell surface —
    `neosian memory`'s engine executed in-process (ledger #78).
    """

    FUNCTION = "function"
    NATIVE = "native_memory"
    CLI = "cli"


@dataclass(frozen=True, slots=True)
class DocumentExpectation:
    """Store truth for one document, checked after a session.

    Exactly one of `path` (this exact document must exist) or
    `path_prefix` (exactly one live document under the prefix must
    satisfy `content` — zero is the fact unrecorded, two is a
    duplicate) names the document: document *naming* is legitimately
    the model's choice, so model-driven scenarios pin the prefix while
    scripted suites may pin the exact path. `content` matchers use
    response semantics (§13.4 — model-authored prose, case-insensitive
    `contains`); `versions` is the exact version count and `actions`
    pins the version history oldest-first, applied to the matched
    document under either key.
    """

    path: str | None = None
    path_prefix: str | None = None
    content: tuple[ValueMatcher, ...] = ()
    versions: int | None = None
    actions: tuple[MemoryAction, ...] | None = None

    def __post_init__(self) -> None:
        if (self.path is None) == (self.path_prefix is None):
            raise ValueError(
                "DocumentExpectation needs exactly one of 'path' or 'path_prefix'"
            )


@dataclass(frozen=True, slots=True)
class StoreExpectation:
    """What the store must hold after a session's last turn.

    `counts` maps a virtual prefix to an exact live-document count (the
    dedup signal); `absent` paths must not exist; `forbidden` strings
    must appear in no live document anywhere (the no-secrets rule).
    """

    documents: tuple[DocumentExpectation, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    absent: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.documents or self.counts or self.absent or self.forbidden)


@dataclass(frozen=True, slots=True)
class SeedDocument:
    """A document that exists before session 1 (DESIGN §13.12, NG).

    Written through the shipped dispatcher with actor `eval:seed` on a
    clock set `age_days` into the past, so seeded documents carry real
    aging evidence — old enough for maintenance's deletion floor by
    default, `age_days: 0` for a deliberately fresh, protected one.
    """

    path: str
    content: str
    age_days: int = 30


@dataclass(frozen=True, slots=True)
class MemorySession:
    """One session: a fresh bare Agent over the scenario's store root.

    The memory index regenerates between sessions (the frozen-index
    rule), so recall is honestly measurable only in a later session.
    `script` runs the session keylessly on its own scripted FakeClient
    — with `reflect` or `maintain`, each engine call consumes the
    script's next turn after the last agent turn. `reflect` runs the
    §15 reflection engine over the session's transcript at session end;
    `maintain` runs the §16 maintenance engine after it (turns are
    optional on a maintain session — a pure gardening step); both run
    before the store check.
    """

    name: str
    turns: tuple[EvalTurn, ...]
    script: tuple[FakeTurn, ...] | None = None
    expect_store: StoreExpectation = field(default_factory=StoreExpectation)
    reflect: bool = False
    maintain: bool = False


@dataclass(frozen=True, slots=True)
class MemoryScenario:
    """One measured behavior, as ordered sessions over one store root.

    The loader enforces all-or-no-`script:` across a scenario's sessions
    — a half-keyless cell would make the throttle rule undefined.
    `seed` documents are planted before session 1.
    """

    name: str
    sessions: tuple[MemorySession, ...]
    seed: tuple[SeedDocument, ...] = ()

    @property
    def is_scripted(self) -> bool:
        return all(s.script is not None for s in self.sessions)


@dataclass(frozen=True, slots=True)
class MemoryEvalConfig:
    """A `kind: memory` suite: transports × models × scenarios.

    `agent` names an AgentConfig *without* `memory=` — the suite owns
    the store (one fresh root per cell) and the mounts. `transports`
    occupies the report's variants axis (ledger #64).
    """

    name: str
    agent: str
    models: tuple[Model, ...]
    mounts: tuple[Mount, ...]
    scenarios: tuple[MemoryScenario, ...]
    transports: tuple[Transport, ...] = (Transport.FUNCTION,)
    execute_tools: frozenset[ToolName] = frozenset()
    ignore_tools: frozenset[ToolName] = frozenset()
    stop_on_failure: bool = True
    throttle_ms: int = 500

    kind: ClassVar[EvalKind] = EvalKind.MEMORY

    @property
    def variant_names(self) -> tuple[str, ...]:
        return tuple(t.value for t in self.transports)

    @property
    def model_names(self) -> tuple[str, ...]:
        return tuple(m.value for m in self.models)

    @property
    def case_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.scenarios)
