"""Terminal projections of existing JSON reports; pipes keep engine bytes."""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from collections.abc import Callable, Mapping
from typing import Any, Final, TextIO

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text
from rich.tree import Tree

from neosian._cli.display import console_for, records, section, terminal
from neosian._cli.render_status import render_status as render_status
from neosian._cli.ui import BRAND_ACCENT
from neosian._foundation.memory.audit import turn_ranges

Engine = Callable[[list[str]], int]
Render = Callable[[dict[str, Any], Console], None]
_UUID: Final = re.compile(
    r"\b([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)


def rendered(out: TextIO, env: Mapping[str, str]) -> bool:
    """Layout depends on the stream; NO_COLOR controls only colour."""
    del env
    return terminal(out)


def run_rendered(
    engine: Engine,
    argv: list[str],
    render: Render,
    *,
    out: TextIO,
    env: Mapping[str, str],
    partial: bool = False,
) -> int:
    """Execute once, projecting reports while preserving stderr and exit tiers.

    Partial reports opt in; error envelopes stay on the engine's stderr.
    Help and literal arguments bypass projection. Unexpected non-JSON output
    is replayed verbatim, never obtained by running an operation again.
    """
    if any(arg in argv for arg in ("--json", "--", "--help", "-h")) or not rendered(
        out, env
    ):
        return engine(argv)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = engine([*argv, "--json"])
    raw = buffer.getvalue()
    try:
        envelope = json.loads(raw)
    except ValueError:
        out.write(raw)
        return code
    if (
        isinstance(envelope, dict)
        and "error" not in envelope
        and (code == 0 or (partial and code == 1))
    ):
        render(envelope, console_for(out, env))
    return code


def _short(text: str) -> str:
    return _UUID.sub(r"\1", text)


def _minute(created_at: str) -> str:
    return created_at[:16].replace("T", " ") + " UTC"


def render_docs(page: dict[str, Any], console: Console) -> None:
    if "body" in page:
        console.print(Markdown(str(page["body"])))
        return
    section(console, "Documentation")
    records(
        console,
        ("Topic", "Summary"),
        [(p["topic"], p["summary"]) for p in page["topics"]],
    )
    sys.stderr.write("hint: neosian docs <topic> prints a page\n")


def render_audit(envelope: dict[str, Any], console: Console) -> None:
    section(console, "Audit")
    rows = []
    for entry in envelope["entries"]:
        if entry["event"] == "turn":
            what = f"{entry['conversation_id']} turn {entry['turn']}"
        elif entry["event"] == "redacted" and entry.get("conversation_id"):
            ranges = turn_ranges(tuple(entry.get("turns", ())))
            what = f"{entry['conversation_id']} turns {ranges}"
        elif entry["event"] == "redacted":
            where = "scope-wide" if entry["path"] is None else f"/{entry['path']}"
            what = f"{entry['count']} at {where}"
        else:
            marker = " (redacted)" if entry["redacted"] else ""
            what = f"/{entry['path']} v{entry['version']}{marker}"
        if entry.get("continues"):
            what += f" continues {', '.join(entry['continues'])}"
        rows.append(
            (
                _minute(entry["created_at"]),
                _short(entry["actor"] or "-"),
                entry["event"],
                _short(what),
            )
        )
    if not rows:
        console.print(Text(f"no ledger entries for scope {envelope['scope']!r}"))
        return
    records(console, ("when", "actor", "event", "what"), rows)


def render_search(envelope: dict[str, Any], console: Console) -> None:
    section(console, "Search")
    if not envelope["hits"]:
        console.print(Text(f"no turn matches every term of {envelope['query']!r}"))
        return
    records(
        console,
        ("where", "when", "actor", "snippet"),
        [
            (
                _short(f"{hit['conversation_id']} #{hit['turn']}"),
                _minute(hit["created_at"]),
                _short(hit["actor"] or "-"),
                hit["snippet"],
            )
            for hit in envelope["hits"]
        ],
    )


def render_index(envelope: dict[str, Any], console: Console) -> None:
    tree = Tree(Text("memory", style=BRAND_ACCENT), guide_style="dim")
    branch = tree
    for line in str(envelope.get("data", "")).splitlines():
        if line.startswith("## "):
            branch = tree.add(Text(line[3:], style="bold"))
        elif line.startswith("- "):
            branch.add(Text(line[2:]))
        elif line.strip():
            branch.add(Text(line.strip(), style="dim"))
    console.print(tree)
