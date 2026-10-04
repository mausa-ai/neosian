"""Shared terminal presentation; application data is always literal text."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TextIO

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from neosian._cli.ui import BRAND_ACCENT


def terminal(out: TextIO) -> bool:
    return bool(getattr(out, "isatty", lambda: False)())


def console_for(out: TextIO, env: Mapping[str, str] | None = None) -> Console:
    env = os.environ if env is None else env
    return Console(
        file=out,
        no_color=bool(env.get("NO_COLOR")),
        markup=False,
        highlight=False,
    )


class _OneLine(logging.Handler):
    """A library warning as one stderr line: the message and its
    exception's own summary, never the traceback."""

    def emit(self, record: logging.LogRecord) -> None:
        line = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            error = record.exc_info[1]
            line = f"{line}: {type(error).__name__}: {error}"
        sys.stderr.write(f"neosian: {line}\n")  # the stream of the moment


@contextmanager
def one_line_warnings() -> Iterator[None]:
    """While open, the library's warnings reach stderr one line each,
    where Python's last resort would print a traceback."""
    logger = logging.getLogger("neosian")
    handler = _OneLine(logging.WARNING)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)


def section(console: Console, title: str) -> None:
    console.print(Text(title, style=f"bold {BRAND_ACCENT}"))


def field_table(rows: Sequence[tuple[str, str]]) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold", overflow="fold", max_width=16)
    table.add_column(overflow="fold")
    for label, value in rows:
        table.add_row(Text(label), Text(value))
    return table


def fields(console: Console, rows: Sequence[tuple[str, str]]) -> None:
    console.print(field_table(rows))


def records(
    console: Console, columns: Sequence[str], rows: Sequence[Sequence[str]]
) -> None:
    """Wide tables become labeled records on a narrow terminal."""
    if console.width < 80:
        for index, row in enumerate(rows):
            if index:
                console.print()
            fields(console, list(zip(columns, row, strict=True)))
        return
    table = Table(
        box=box.SIMPLE_HEAD,
        header_style=f"bold {BRAND_ACCENT}",
        pad_edge=False,
        collapse_padding=True,
    )
    for column in columns:
        table.add_column(Text(column), overflow="fold")
    for row in rows:
        table.add_row(*(Text(value) for value in row))
    console.print(table)


def note(console: Console, text: str) -> None:
    console.print(Text(f"• {text}"))
