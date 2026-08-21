"""The `neosian memory` engine — grammar, store lifetime, rendering.

The shell is a transport: the six commands ride the shared dispatcher
(`dispatch.py`), the store flags ride the shared grammar (`settings.py`),
and `--json` prints the function tool's `ToolResult` envelope verbatim
(ledger #77) — so the CLI cannot drift from the other transports. The
engine is async and stream-injected: the entry tier
(`neosian/memory/cli.py`) wraps it in `asyncio.run` over real streams,
while the eval harness calls it in-process from a running loop.

Exit tiering (DESIGN §14.1): 0 success · 1 the command ran and failed
(a corrective dispatch failure, rendered `error:`/`hint:`) · 2 the argv
was wrong (grammar, unknown command, bad scope or mount — nothing is
constructed) · 130 interrupt (entry tier).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TextIO

if TYPE_CHECKING:
    from _typeshed import SupportsWrite

from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.settings import (
    StoreSettings,
    add_store_arguments,
    resolve_store_settings,
)
from neosian._foundation.shared.exceptions import MemoryStoreError
from neosian._foundation.tools.base import ToolResult

_DESCRIPTION: Final = "Read and write neosian agent memory from the shell."
_EPILOG: Final = (
    "Postgres: set NEOSIAN_POSTGRES_DSN instead of --root (a DSN never "
    "belongs in argv; the key is shared with `neosian mcp`). One writer "
    "per FileStore root (DESIGN §8). --json prints the memory tool's "
    "envelope verbatim; pass '-' to --content/--new-str/--insert-text "
    "to read the text from stdin."
)

# argv flag -> dispatcher argument, per command; `vars(args)` filtered —
# there is no translation table to drift (flags are the dispatcher's
# argument names, kebab-cased).
ARGUMENT_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "view": ("path", "view_range"),
    "create": ("path", "content"),
    "str_replace": ("path", "old_str", "new_str"),
    "insert": ("path", "insert_line", "insert_text"),
    "delete": ("path",),
    "rename": ("old_path", "new_path"),
}
# The one payload flag per command that accepts '-' for stdin.
_STDIN_KEYS: Final = ("content", "new_str", "insert_text")


@dataclass(frozen=True, slots=True)
class _Request:
    """A fully parsed invocation — the typed boundary after argparse."""

    settings: StoreSettings
    command: str
    arguments: dict[str, Any]
    json_output: bool


class _StreamParser(argparse.ArgumentParser):
    """argparse over injected streams.

    The engine runs in-process inside the eval harness, so it must not
    write to (or swap) the process's real stdio; help and errors go to
    the streams `bind` sets.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.out: TextIO = sys.stdout
        self.err: TextIO = sys.stderr

    def bind(self, out: TextIO, err: TextIO) -> None:
        self.out = out
        self.err = err

    def _print_message(
        self, message: str, file: SupportsWrite[str] | None = None
    ) -> None:
        if message:
            target = self.err if file is sys.stderr else self.out
            target.write(message)


def _build_parser(
    prog: str, out: TextIO, err: TextIO
) -> tuple[_StreamParser, dict[str, _StreamParser]]:
    parser = _StreamParser(prog=prog, description=_DESCRIPTION, epilog=_EPILOG)
    parser.bind(out, err)
    subparsers = parser.add_subparsers(dest="command", metavar="command", required=True)

    def command(name: str, help_text: str) -> _StreamParser:
        sub = subparsers.add_parser(name, help=help_text, epilog=_EPILOG)
        assert isinstance(sub, _StreamParser)  # parser_class defaults to type(self)
        sub.bind(out, err)
        return sub

    view = command("view", "print a document, a directory listing, or / (the index)")
    view.add_argument("path", nargs="?", default="/", help="virtual path (default: /)")
    view.add_argument(
        "--view-range",
        nargs=2,
        type=int,
        metavar=("START", "END"),
        help="1-based inclusive line range; END -1 means end of document",
    )

    create = command("create", "create or overwrite a document")
    create.add_argument("path", help="virtual path, e.g. /memories/topic-name")
    create.add_argument("--content", required=True, help="document text ('-' = stdin)")

    str_replace = command("str_replace", "replace exactly one occurrence")
    str_replace.add_argument("path", help="virtual path")
    str_replace.add_argument("--old-str", required=True, help="text to replace")
    str_replace.add_argument(
        "--new-str", required=True, help="replacement text ('-' = stdin)"
    )

    insert = command("insert", "splice text at a line number")
    insert.add_argument("path", help="virtual path")
    insert.add_argument("--insert-line", required=True, type=int, help="0-based line")
    insert.add_argument(
        "--insert-text", required=True, help="text to insert ('-' = stdin)"
    )

    delete = command("delete", "delete a document")
    delete.add_argument("path", help="virtual path")

    rename = command("rename", "move a document (cross-mount is not atomic)")
    rename.add_argument("old_path", metavar="OLD", help="current virtual path")
    rename.add_argument("new_path", metavar="NEW", help="destination virtual path")

    subs = {
        "view": view,
        "create": create,
        "str_replace": str_replace,
        "insert": insert,
        "delete": delete,
        "rename": rename,
    }
    for sub in subs.values():
        add_store_arguments(sub, default_actor="cli")
        sub.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="print the memory tool's JSON envelope on stdout",
        )
    return parser, subs


def _parse(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    stdin: TextIO,
    out: TextIO,
    err: TextIO,
    prog: str,
) -> _Request:
    parser, subs = _build_parser(prog, out, err)
    args = parser.parse_args(list(argv))
    command: str = args.command
    settings = resolve_store_settings(subs[command], args, env)
    arguments = {key: getattr(args, key) for key in ARGUMENT_KEYS[command]}
    for key in _STDIN_KEYS:
        if arguments.get(key) == "-":
            arguments[key] = stdin.read()
    return _Request(
        settings=settings,
        command=command,
        arguments=arguments,
        json_output=args.json_output,
    )


async def _execute(request: _Request) -> ToolResult[str]:
    settings = request.settings
    if settings.dsn is not None:
        # Function-local: a module-level edge memory -> postgres would
        # close a package cycle (postgres implements this package's ABC).
        from neosian._foundation.postgres.store import PostgresStore

        store = PostgresStore(settings.dsn, schema=settings.schema)
        try:
            return await _dispatch_on(store, request)
        finally:
            await store.aclose()
    assert settings.root is not None  # resolve_store_settings guarantees one
    return await _dispatch_on(FileStore(settings.root), request)


async def _dispatch_on(store: MemoryStore, request: _Request) -> ToolResult[str]:
    config = MemoryConfig(store=store, mounts=request.settings.mounts)
    return await dispatch(
        config,
        request.command,
        request.arguments,
        actor=request.settings.actor,
    )


def _render(
    result: ToolResult[str], *, json_output: bool, out: TextIO, err: TextIO
) -> int:
    if json_output:
        out.write(result.to_json() + "\n")
    elif result.success:
        if result.data:
            out.write(result.data if result.data.endswith("\n") else result.data + "\n")
        if result.system_reminder:
            err.write(f"hint: {result.system_reminder}\n")
    else:
        err.write(f"error: {result.error}\n")
        if result.system_reminder:
            err.write(f"hint: {result.system_reminder}\n")
    return 0 if result.success else 1


async def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    stdin: TextIO,
    out: TextIO,
    err: TextIO,
    prog: str = "neosian memory",
) -> int:
    """Parse and execute one memory command; construct nothing on exit 2."""
    try:
        request = _parse(argv, env, stdin=stdin, out=out, err=err, prog=prog)
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    except MemoryStoreError as exc:  # Mount() scope/path validation
        err.write(f"error: [{exc.code}] {exc.message}\n")
        return 2
    result = await _execute(request)
    return _render(result, json_output=request.json_output, out=out, err=err)
