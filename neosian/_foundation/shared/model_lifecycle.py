"""Release-owned model selectors and the 30-day migration policy.

Resolution never reads a clock or the network. Publication dates are stamped
at release preparation, not guessed while an unreleased change is built.
"""

import warnings
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from pathlib import Path

from neosian._foundation.shared.models import Model


class ModelSelector(str, Enum):
    """Follow a provider tier across neosian releases; Model pins an exact ID."""

    CLAUDE_SONNET_DEFAULT = "anthropic:sonnet:default"
    CLAUDE_SONNET_LATEST = "anthropic:sonnet:latest"
    CLAUDE_OPUS_DEFAULT = "anthropic:opus:default"
    CLAUDE_OPUS_LATEST = "anthropic:opus:latest"
    CLAUDE_FABLE_DEFAULT = "anthropic:fable:default"
    CLAUDE_FABLE_LATEST = "anthropic:fable:latest"
    GPT_SOL_DEFAULT = "openai:sol:default"
    GPT_SOL_LATEST = "openai:sol:latest"
    GPT_ASTRA_DEFAULT = "openai:astra:default"
    GPT_ASTRA_LATEST = "openai:astra:latest"
    GPT_LUNA_DEFAULT = "openai:luna:default"
    GPT_LUNA_LATEST = "openai:luna:latest"
    ANTHROPIC_DEFAULT = "anthropic:default"
    ANTHROPIC_LATEST = "anthropic:latest"
    OPENAI_DEFAULT = "openai:default"
    OPENAI_LATEST = "openai:latest"


class ModelTransitionWarning(FutureWarning):
    """A model selection will change or retire in a future neosian release."""


@dataclass(frozen=True)
class Transition:
    """Strings keep migration advice readable after an enum member leaves."""

    predecessor: str
    successor: str
    release: str
    family: str


# None is deliberate: publication has not happened. The release gate refuses
# a tag until its UTC date is recorded here and in the changelog.
RELEASE_DATES: dict[str, date | None] = {"1.4.0": None}
TRANSITIONS = (
    Transition("claude-sonnet-5", "claude-sonnet-5-5", "1.4.0", "anthropic:sonnet"),
    Transition("gpt-6-sol", "gpt-6.1-sol", "1.4.0", "openai:sol"),
    Transition("gpt-5.1-2025-11-13", "gpt-6.1-sol", "1.4.0", "openai:sol"),
)

# Explicit release snapshots. No wall-clock routing; release validation below
# checks these against the migration history, including overlapping successors.
DEFAULTS = {
    "anthropic:sonnet": Model.CLAUDE_SONNET_5,
    "anthropic:opus": Model.CLAUDE_OPUS_5_5,
    "anthropic:fable": Model.CLAUDE_FABLE_5_1,
    "openai:sol": Model.GPT_6_SOL,
    "openai:astra": Model.GPT_6_ASTRA,
    "openai:luna": Model.GPT_6_LUNA,
}
LATEST = {
    **DEFAULTS,
    "anthropic:sonnet": Model.CLAUDE_SONNET_5_5,
    "openai:sol": Model.GPT_6_1_SOL,
}
_PROVIDER_FAMILY = {"anthropic": "anthropic:sonnet", "openai": "openai:sol"}
WINDOW = timedelta(days=30)
_PACKAGE_PATH = str(Path(__file__).resolve().parents[2])
_INITIAL_DEFAULTS = {
    "anthropic:sonnet": "claude-sonnet-5",
    "anthropic:opus": "claude-opus-5-5",
    "anthropic:fable": "claude-fable-5-1",
    "openai:sol": "gpt-6-sol",
    "openai:astra": "gpt-6-astra",
    "openai:luna": "gpt-6-luna",
}


def selector_model(value: str) -> Model | None:
    """Resolve only the documented selector namespace, without warnings."""
    if value not in ModelSelector._value2member_map_:
        return None
    family, _, kind = value.rpartition(":")
    family = _PROVIDER_FAMILY.get(family, family)
    return (LATEST if kind == "latest" else DEFAULTS)[family]


def deadline(transition: Transition) -> date | None:
    """The first eligible release date, independently for each successor."""
    shipped = RELEASE_DATES[transition.release]
    return shipped + WINDOW if shipped is not None else None


def transition_notice(value: str) -> str | None:
    """One actionable notice, also used in the CLI picker."""
    for transition in TRANSITIONS:
        if transition.predecessor != value:
            continue
        when = deadline(transition)
        timing = (
            f"the first neosian release on or after {when.isoformat()} UTC"
            if when is not None
            else f"a release at least 30 days after neosian {transition.release} "
            "is published (publication date pending)"
        )
        return (
            f"{value} is superseded by {transition.successor}; it will leave the "
            f"supported catalog in {timing}. Its tier default advances after the "
            "same window. An installed neosian version does not switch by date. "
            f"Select {transition.family}:latest to move now, or pin "
            f"{transition.successor}."
        )
    return None


def warn_transition(value: str) -> None:
    """Warn on selection, never merely from importing the library."""
    if notice := transition_notice(value):
        warnings.warn(
            notice,
            ModelTransitionWarning,
            stacklevel=2,
            skip_file_prefixes=(_PACKAGE_PATH,),
        )


def retired_notice(value: str) -> str | None:
    """Keep exact-ID failures helpful without silently redirecting pins."""
    if value not in Model._value2member_map_ and (notice := transition_notice(value)):
        return f"{value} is retired from this catalog. {notice}"
    return None


def eligible_successor(family: str, *, on: date) -> str | None:
    """Newest independently aged successor, for release tooling only."""
    eligible = [
        (when, index, transition.successor)
        for index, transition in enumerate(TRANSITIONS)
        if transition.family == family
        and (when := deadline(transition)) is not None
        and when <= on
    ]
    return max(eligible)[2] if eligible else None


def release_errors(*, version: str, on: date) -> list[str]:
    """Refuse unstamped releases, early removals and stale promotions."""
    errors: list[str] = []
    if version in RELEASE_DATES and RELEASE_DATES[version] != on:
        errors.append(f"Stamp neosian {version}'s publication date as {on} UTC")
    for transition in TRANSITIONS:
        when = deadline(transition)
        present = transition.predecessor in Model._value2member_map_
        if when is None:
            errors.append(f"Missing publication date for {transition.release}")
        elif on >= when and present:
            errors.append(f"Retire {transition.predecessor}: window ended {when}")
        elif on < when and not present:
            errors.append(f"Keep {transition.predecessor} until {when}")
    for family, default in DEFAULTS.items():
        expected = eligible_successor(family, on=on) or _INITIAL_DEFAULTS[family]
        if default.value != expected:
            errors.append(f"Promote {family}:default to {expected}")
    return errors
