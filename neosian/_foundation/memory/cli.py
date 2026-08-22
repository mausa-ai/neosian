"""The `neosian memory` engine — grammar, store lifetime, rendering.

The shell is a transport: the six commands ride the shared dispatcher
(`dispatch.py`), the store flags ride the shared grammar (`settings.py`),
and `--json` prints the function tool's `ToolResult` envelope verbatim
(ledger #77) — so the CLI cannot drift from the other transports. The
`maintain` verb (DESIGN §16) is not a dispatch command: it runs the
gardener engine (`maintenance.py`) — keyless by default, `--model` adds
the semantic pass through an injected client factory — and prints its
own JSON envelope. The engine is async and stream-injected: the entry
tier (`neosian/memory/cli.py`) wraps it in `asyncio.run` over real
streams, while the eval harness calls it in-process from a running loop.

Exit tiering (DESIGN §14.1): 0 success · 1 the command ran and failed
(a corrective dispatch failure, rendered `error:`/`hint:`) · 2 the argv
was wrong (grammar, unknown command, bad scope or mount — nothing is
constructed) · 130 interrupt (entry tier).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final, TextIO

from neosian._foundation.memory.base import MemoryStore
from neosian._foundation.memory.dispatch import dispatch
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.maintenance import (
    MAINTENANCE_MIN_AGE_DAYS,
    MaintenanceResult,
    run_maintenance,
)
from neosian._foundation.memory.mounts import MemoryConfig
from neosian._foundation.memory.settings import (
    StoreSettings,
    StreamParser,
    add_store_arguments,
    resolve_store_settings,
)
from neosian._foundation.shared.exceptions import MemoryStoreError, NeosianError
from neosian._foundation.shared.types import Model, format_micro_usd
from neosian._foundation.tools.base import ToolResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from neosian._foundation.llm.base import BaseLLMClient
    from neosian._foundation.shared.types import Provider

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
    """A fully parsed invocation — the typed boundary after argparse.

    `model`/`min_age_days` are the maintain verb's; the six dispatch
    commands leave them at their defaults."""

    settings: StoreSettings
    command: str
    arguments: dict[str, Any]
    json_output: bool
    model: Model | None = None
    min_age_days: int = MAINTENANCE_MIN_AGE_DAYS


def _build_parser(
    prog: str, out: TextIO, err: TextIO
) -> tuple[StreamParser, dict[str, StreamParser]]:
    parser = StreamParser(prog=prog, description=_DESCRIPTION, epilog=_EPILOG)
    parser.bind(out, err)
    subparsers = parser.add_subparsers(dest="command", metavar="command", required=True)

    def command(name: str, help_text: str) -> StreamParser:
        sub = subparsers.add_parser(name, help=help_text, epilog=_EPILOG)
        assert isinstance(sub, StreamParser)  # parser_class defaults to type(self)
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

    # The maintain verb (DESIGN §16) is not a dispatch command: it runs the
    # gardener over the writable mounts — keyless by default, --model adds
    # the semantic pass — so its --json envelope is its own (§14.2).
    maintain = command(
        "maintain",
        "consolidate the store: prune empty documents, merge duplicates"
        " (--model adds the semantic pass)",
    )
    add_store_arguments(maintain, default_actor="cli")
    maintain.add_argument(
        "--model",
        metavar="MODEL",
        help="run the model stage on this model (needs its provider key);"
        " omitted, only the deterministic stage runs",
    )
    maintain.add_argument(
        "--min-age-days",
        type=int,
        default=MAINTENANCE_MIN_AGE_DAYS,
        metavar="N",
        help="never delete documents updated in the last N days"
        f" (default {MAINTENANCE_MIN_AGE_DAYS})",
    )
    maintain.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="print the maintenance result as JSON on stdout",
    )
    subs["maintain"] = maintain
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
    if command == "maintain":
        if args.min_age_days < 0:
            subs[command].error("--min-age-days must not be negative")
        return _Request(
            settings=settings,
            command=command,
            arguments={},
            json_output=args.json_output,
            model=_parse_model(subs[command], args.model),
            min_age_days=args.min_age_days,
        )
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


def _parse_model(sub: StreamParser, value: str | None) -> Model | None:
    """A model id from argv, `provider:model` accepted (the eval spelling);
    unknown ids are grammar-tier errors — nothing is constructed."""
    if value is None:
        return None
    bare = value.split(":", 1)[1] if ":" in value else value
    for model in Model:
        if model.value == bare:
            return model
    known = ", ".join(m.value for m in Model)
    sub.error(f"unknown model {value!r} (known: {known})")
    raise AssertionError("unreachable")  # error() raises SystemExit


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


async def _execute_maintain(
    request: _Request,
    client_factory: Callable[[Provider], BaseLLMClient] | None,
    *,
    out: TextIO,
    err: TextIO,
) -> int:
    settings = request.settings
    client: BaseLLMClient | None = None
    acquire: Callable[[Provider], BaseLLMClient] | None = None
    if request.model is not None:
        if client_factory is None:
            err.write("error: --model is not available from this entry point\n")
            return 2
        try:
            # Eager, so a missing provider key is loud at construction
            # (the #84 rule) instead of degrading inside the model stage.
            client = client_factory(request.model.provider)
        except NeosianError as exc:
            err.write(f"error: [{exc.code}] {exc.message}\n")
            return 2
        acquire = _lease(client)
    try:
        if settings.dsn is not None:
            from neosian._foundation.postgres.store import PostgresStore

            store = PostgresStore(settings.dsn, schema=settings.schema)
            try:
                result = await _maintain_on(store, request, acquire)
            finally:
                await store.aclose()
        else:
            assert settings.root is not None
            result = await _maintain_on(FileStore(settings.root), request, acquire)
    finally:
        if client is not None:
            await client.close()
    return _render_maintenance(result, request, out=out, err=err)


def _lease(client: BaseLLMClient) -> Callable[[Provider], BaseLLMClient]:
    """The one constructed client as an acquire lease — the caller
    (`_execute_maintain`) owns its lifetime and closes it."""

    def acquire(_: Provider) -> BaseLLMClient:
        return client

    return acquire


async def _maintain_on(
    store: MemoryStore,
    request: _Request,
    acquire: Callable[[Provider], BaseLLMClient] | None,
) -> MaintenanceResult:
    config = MemoryConfig(store=store, mounts=request.settings.mounts)
    return await run_maintenance(
        config,
        acquire=acquire,
        model=request.model,
        actor=request.settings.actor,
        min_age=timedelta(days=request.min_age_days),
    )


def _render_maintenance(
    result: MaintenanceResult, request: _Request, *, out: TextIO, err: TextIO
) -> int:
    cost = (
        result.usage.cost_micro_usd(request.model)
        if result.usage is not None and request.model is not None
        else None
    )
    if request.json_output:
        envelope = {
            "writes": [asdict(write) for write in result.writes],
            "model": result.model,
            "usage": None if result.usage is None else asdict(result.usage),
            "cost_micro_usd": cost,
        }
        out.write(json.dumps(envelope) + "\n")
    else:
        for write in result.writes:
            suffix = "" if write.version is None else f" (v{write.version})"
            out.write(f"{write.command} {write.path}{suffix}\n")
        if not result.writes:
            out.write("nothing to do\n")
        if result.usage is not None:
            spend = "unknown" if cost is None else format_micro_usd(cost)
            out.write(
                f"model pass: {result.model},"
                f" {result.usage.total_tokens} tokens, {spend}\n"
            )
    if request.model is not None and result.model is None:
        # The stage was asked for and degraded — the explicit shell tells
        # the operator, unlike the library's close paths (§16).
        err.write("error: the model stage failed; deterministic actions still landed\n")
        return 1
    return 0


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
    client_factory: Callable[[Provider], BaseLLMClient] | None = None,
) -> int:
    """Parse and execute one memory command; construct nothing on exit 2.

    `client_factory` powers `maintain --model` only — the entry tiers
    supply the real router-backed one; this package may not construct
    provider clients itself (the storage import contract, DESIGN §1)."""
    try:
        request = _parse(argv, env, stdin=stdin, out=out, err=err, prog=prog)
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    except MemoryStoreError as exc:  # Mount() scope/path validation
        err.write(f"error: [{exc.code}] {exc.message}\n")
        return 2
    if request.command == "maintain":
        return await _execute_maintain(request, client_factory, out=out, err=err)
    result = await _execute(request)
    return _render(result, json_output=request.json_output, out=out, err=err)
