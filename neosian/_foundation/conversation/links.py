"""Link handles (DESIGN §23, NJ) — pure functions, no I/O.

An *atom* is a token a clip may never split: a URL, a path, a long id, or
a handle. A `LinkRegistry` numbers the atoms of a history by first
appearance in turn order, so `[link N]` is a pure function of the
append-only log — appending turns never renumbers, a handle written into
a checkpointed log line stays valid forever, and nothing is stored.
Handles are contracted into log lines only and expanded inside tool-call
arguments only; the model's own text is never rewritten. Another
conversation's history numbers independently under the qualified form
`[link <conversation_id>:N]`; the bare form is the reader's own.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

from neosian._foundation.llm.base import text_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from neosian._foundation.conversation.types import ConversationTurn

# A token registers as a link from this length: shorter paths and ids
# read better verbatim than a handle and cost about as many tokens.
LINK_CHARS: Final = 48
_HANDLE: Final = r"\[link [^\]]*\]"
# One character of a token: never whitespace, a quote, an angle bracket,
# the log separator, a square bracket, a backtick or a backslash; a
# balanced parenthesis group counts as part of the token (Foo_(bar)).
_CHAR: Final = r"""[^\s"'<>|\[\]`\\()]"""
_UNIT: Final = rf"(?:{_CHAR}|\({_CHAR}*\))"
# A token ends where trailing punctuation meets a delimiter or the end.
_END: Final = r"""(?=[.,;:!?)]*(?:[\s"'<>|\[\]`\\)]|$))"""
_URL: Final = rf"[A-Za-z][A-Za-z0-9+.-]*://{_UNIT}+?{_END}"
_PATH: Final = rf"{_UNIT}*?/{_UNIT}*?{_END}"
_ID: Final = (
    rf"(?=[A-Za-z0-9_.-]*\d)[A-Za-z0-9_-](?:[A-Za-z0-9_.-]*[A-Za-z0-9_-])?{_END}"
)
_ATOM: Final = re.compile(rf"{_HANDLE}|(?<!{_CHAR})(?:{_URL}|{_PATH}|{_ID})")


def boundary(text: str, at: int) -> tuple[int, int]:
    """The span of the atom `at` falls inside, or `(at, at)` — a clip cuts
    at the start (head) or the end (tail) of that span, never within."""
    for match in _ATOM.finditer(text):
        if match.start() < at < match.end():
            return match.start(), match.end()
        if match.start() >= at:
            break
    return at, at


def _links(text: str) -> Iterator[str]:
    """The registrable atoms of `text`, in order: long, and not a handle."""
    for match in _ATOM.finditer(text):
        token = match.group()
        if len(token) >= LINK_CHARS and not token.startswith("[link "):
            yield token


def _strings(value: object) -> Iterator[str]:
    """Every string inside a JSON-shaped value, in order."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


class LinkRegistry:
    """Tokens numbered by first appearance; `source` names another
    conversation and qualifies its handles."""

    def __init__(self, tokens: Iterable[str] = (), *, source: str | None = None):
        self._numbers: dict[str, int] = {}
        for token in tokens:
            self._numbers.setdefault(token, len(self._numbers) + 1)
        self._tokens = tuple(self._numbers)
        self._label = "" if source is None else f"{source}:"
        self._handles = re.compile(rf"\[link {re.escape(self._label)}(\d+)\]")

    @classmethod
    def of(
        cls, turns: Iterable[ConversationTurn], *, source: str | None = None
    ) -> LinkRegistry:
        """Turns in order, messages in provider order, a message's text
        then its tool-call argument strings; reasoning is model-internal
        and skipped."""

        def tokens() -> Iterator[str]:
            for turn in turns:
                for message in turn.messages:
                    yield from _links(text_of(message))
                    for call in message.tool_calls:
                        for text in _strings(call.arguments):
                            yield from _links(text)

        return cls(tokens(), source=source)

    def __len__(self) -> int:
        return len(self._tokens)

    def handle(self, number: int) -> str:
        return f"[link {self._label}{number}]"

    def contract(self, text: str) -> str:
        """Every registered token → its handle, by whole-atom match; an
        unregistered token or an existing handle passes through."""

        def swap(match: re.Match[str]) -> str:
            number = self._numbers.get(match.group())
            return match.group() if number is None else self.handle(number)

        return _ATOM.sub(swap, text)

    def expand(self, value: Any) -> Any:
        """Every handle of this registry → its token, through strings,
        dicts and lists; an unknown number is left as written."""
        if isinstance(value, str):
            return self._handles.sub(self._token, value)
        if isinstance(value, dict):
            return {key: self.expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.expand(item) for item in value]
        return value

    def _token(self, match: re.Match[str]) -> str:
        number = int(match.group(1))
        if 1 <= number <= len(self._tokens):
            return self._tokens[number - 1]
        return match.group()
