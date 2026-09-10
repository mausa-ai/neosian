"""The rendered verbs (DESIGN §30.3, ledger #204): on a terminal `docs
<topic>` is markdown, `audit` a table, `memory view /` a tree, `status` a
table — restrained Rich, one accent, no panels or boxes inside verbs.
Under a pipe, `NO_COLOR` or `--json` the engine writes its own bytes to
the real streams, byte-identical to before: the rendering is a
projection of the verb's `--json` envelope, never a second code path.
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Callable, Mapping
from typing import Any, TextIO

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.tree import Tree

from neosian._cli.ui import BRAND_ACCENT

Engine = Callable[[list[str]], int]
Render = Callable[[dict[str, Any], Console], None]


def rendered(out: TextIO, env: Mapping[str, str]) -> bool:
    """A terminal, and no `NO_COLOR`: the only place a verb renders."""
    isatty = getattr(out, "isatty", None)
    return bool(isatty and isatty()) and not env.get("NO_COLOR")


def run_rendered(
    engine: Engine,
    argv: list[str],
    render: Render,
    *,
    out: TextIO,
    env: Mapping[str, str],
) -> int:
    """Run the engine on the real streams — or, on a terminal without
    `--json`, run it with `--json` into a buffer and render the envelope.
    A non-zero exit renders nothing: the engine's stderr text stands."""
    if "--json" in argv or not rendered(out, env):
        return engine(argv)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = engine([*argv, "--json"])
    if code != 0:
        return code
    render(json.loads(buffer.getvalue()), Console(file=out))
    return 0


def _table(*columns: str) -> Table:
    table = Table(box=None, header_style=f"bold {BRAND_ACCENT}", pad_edge=False)
    for column in columns:
        table.add_column(column)
    return table


def render_docs(page: dict[str, Any], console: Console) -> None:
    console.print(Markdown(str(page["body"])))


def render_audit(envelope: dict[str, Any], console: Console) -> None:
    table = _table("when", "actor", "event", "what")
    for entry in envelope["entries"]:
        if entry["event"] == "turn":
            what = f"{entry['conversation_id']} turn {entry['turn']}"
        elif entry["event"] == "redacted":
            where = "scope-wide" if entry["path"] is None else f"/{entry['path']}"
            what = f"{entry['count']} at {where}"
        else:
            marker = " (redacted)" if entry["redacted"] else ""
            what = f"/{entry['path']} v{entry['version']}{marker}"
        table.add_row(entry["created_at"], entry["actor"] or "-", entry["event"], what)
    if not envelope["entries"]:
        console.print(f"no ledger entries for scope {envelope['scope']!r}")
        return
    console.print(table)


def render_index(envelope: dict[str, Any], console: Console) -> None:
    """The memory index as a tree: a `## /mount — description` line opens
    a branch, a `- /path` line is a leaf under it."""
    tree = Tree("memory", style=BRAND_ACCENT, guide_style="dim")
    branch = tree
    for line in str(envelope.get("data", "")).splitlines():
        if line.startswith("## "):
            branch = tree.add(line[3:], style="bold")
        elif line.startswith("- "):
            branch.add(line[2:], style="default")
        elif line.strip():
            branch.add(line.strip(), style="dim")
    console.print(tree)


def render_status(status: dict[str, Any], console: Console) -> None:
    table = _table("", "")
    table.add_row("neosian", f"{status['version']}  ({status['shape']})")
    table.add_row("upgrade", status["upgrade"])
    table.add_row(
        "home",
        f"{status['home']}  ({'exists' if status['home_exists'] else 'missing'})",
    )
    table.add_row(
        "config",
        f"{status['config_path']}  "
        f"({'exists' if status['config_exists'] else 'missing'})",
    )
    keyed = [f"{p['name']} ({p['source']})" for p in status["providers"] if p["source"]]
    table.add_row("keys", ", ".join(keyed) if keyed else "none — neosian configure")
    if status["scopes"]:
        table.add_row(
            "scopes", "  ".join(f"{k} = {v}" for k, v in status["scopes"].items())
        )
    else:
        table.add_row("scopes", str(status["scopes_error"]))
    session = status["last_session"]
    table.add_row(
        "session",
        (
            f"{session['agent']} {session['conversation']}  {session['updated_at']}"
            if session
            else "none recorded"
        ),
    )
    table.add_row("update", f"mode {status['update_mode']}")
    console.print(table)
    clients = _table("client", "installed", "mcp", "hooks", "interpreter")
    for c in status["clients"]:
        clients.add_row(
            c["client"],
            "yes" if c["installed"] else "no",
            "registered" if c["mcp_registered"] else "-",
            "present" if c["hooks_present"] else "-",
            (
                "-"
                if c["interpreter_resolves"] is None
                else (
                    "ok"
                    if c["interpreter_resolves"]
                    else f"missing: {c['interpreter']}"
                )
            ),
        )
    console.print(clients)
    for note in status["one_writer"]:
        console.print(f"note: {note}", style="dim")
