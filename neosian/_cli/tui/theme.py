"""The session's theme: the terminal's own background and palette (as
Claude Code and Codex draw), with the two brand colours."""

from dataclasses import replace
from typing import Final

from textual.theme import BUILTIN_THEMES

from neosian._cli.ui import BRAND_ACCENT, BRAND_SUPPORT

_ANSI: Final = BUILTIN_THEMES["ansi-dark"]
THEME: Final = replace(
    _ANSI,
    name="neosian",
    primary=BRAND_ACCENT,
    secondary=BRAND_SUPPORT,
    variables={
        **_ANSI.variables,
        **{f"markdown-h{level}-color": BRAND_ACCENT for level in range(1, 7)},
        "markdown-h2-text-style": "bold",
        "scrollbar": "ansi_bright_black",
        "scrollbar-hover": "ansi_white",
        "scrollbar-active": "ansi_white",
        "scrollbar-background": "ansi_default",
        "scrollbar-background-hover": "ansi_default",
        "scrollbar-background-active": "ansi_default",
    },
)
