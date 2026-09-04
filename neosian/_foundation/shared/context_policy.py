"""Proactive context-window fitting (DESIGN §5, N0).

`ContextPolicy` is the proactive producer of `ContextWindowExceededError`:
a cheap, provider-agnostic token estimate checked once per attempt, before
the first LLM call, so an egregiously oversized prompt fails fast and free.

The estimate **deliberately underestimates** (character heuristic, flat
low-ball media constants) so it only fires on clear overflow — a false
positive would block a legitimate request, while a miss just falls through
to the reactive backstop, `wrap_provider_error`'s 400 classifier. Attached
to every agent by default; pass `AgentConfig(context_policy=None)` to
disable.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from neosian._foundation.shared.registry import provider_label

if TYPE_CHECKING:
    from neosian._foundation.llm.base import Message
    from neosian._foundation.shared.types import AnyModel

# Underestimating constants: real prompts run ~4 chars/token in English and
# fewer in code; media blocks cost far more than this on every provider.
_TOKENS_PER_MESSAGE: Final = 4
_TOKENS_PER_MEDIA_BLOCK: Final = 256


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Token counting + window fitting for the agent's pre-call check.

    `chars_per_token` divides total prompt characters; 4 is the classic
    English heuristic and an underestimate for code and non-Latin scripts —
    exactly the conservative direction this check wants. `estimator`, when
    set, replaces that heuristic with the caller's own count over the
    prompt messages (a real tokenizer, say); it must underestimate too.

    What is counted, either way: the messages handed to the model —
    text content, reasoning, tool-call names and arguments, a flat
    constant per media block. **Not counted**: the tool schemas sent
    beside them, provider framing of the system prompt, and tool-call
    ids — every one of them makes the real prompt larger, never smaller,
    which keeps this check on the conservative side (TG-44).
    """

    chars_per_token: int = 4
    estimator: Callable[[Sequence[Message]], int] | None = None

    def estimate_tokens(self, messages: Sequence[Message]) -> int:
        """Deliberately-low token estimate for a prompt.

        Counts text content, reasoning, and tool-call arguments by
        characters; media blocks contribute a flat low-ball constant
        (duck-typed on `.text` so this module never imports the block
        classes at runtime). A configured `estimator` replaces all of it.
        """
        if self.estimator is not None:
            return self.estimator(messages)
        chars = 0
        media_blocks = 0
        for message in messages:
            content = message.content
            if isinstance(content, str):
                chars += len(content)
            elif content is not None:
                for block in content:
                    text = getattr(block, "text", None)
                    if text is not None:
                        chars += len(text)
                    else:
                        media_blocks += 1
            if message.reasoning:
                chars += len(message.reasoning)
            for tool_call in message.tool_calls:
                chars += len(tool_call.name) + len(str(tool_call.arguments))
        return (
            chars // self.chars_per_token
            + len(messages) * _TOKENS_PER_MESSAGE
            + media_blocks * _TOKENS_PER_MEDIA_BLOCK
        )

    def ensure_fits(self, model: AnyModel, messages: Sequence[Message]) -> None:
        """Raise ContextWindowExceededError when the prompt clearly overflows.

        Raises:
            ContextWindowExceededError: estimate exceeds the model's
                context window; carries the window and the estimate.
        """
        # Lazy import: exceptions imports constants imports types imports
        # this module (types.py __post_init__ uses the same idiom).
        from neosian._foundation.shared.exceptions import ContextWindowExceededError

        estimated = self.estimate_tokens(messages)
        if estimated > model.context_window:
            raise ContextWindowExceededError(
                model.value,
                context_window=model.context_window,
                estimated_tokens=estimated,
                provider=provider_label(model),
            )
