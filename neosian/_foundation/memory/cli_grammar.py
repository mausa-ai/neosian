"""The `neosian memory` grammar — argparse to the typed `Request`.

Pure and I/O-free (stdin excepted, read only when a payload flag is
`'-'`): nothing here constructs a store or touches the filesystem, which
is what makes exit 2 mean "nothing on disk" (§14.1). The six commands'
flags are the dispatcher's argument names kebab-cased (`ARGUMENT_KEYS` —
no translation table to drift; the eval cli transport reads it); the
maintain and operator verbs are deliberately absent from that table so a
model emitting their names through a dispatch surface gets the grammar's
exit-2 answer, never a real operator action.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, TextIO

from neosian._foundation.memory.maintenance import MAINTENANCE_MIN_AGE_DAYS
from neosian._foundation.memory.settings import (
    StoreSettings,
    StreamParser,
    add_store_arguments,
    resolve_store_settings,
)
from neosian._foundation.shared.registry import lookup_model, registered_models
from neosian._foundation.shared.types import AnyModel, Model

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

# The operator verbs (§14.2) beside maintain: keyless audit and remedy
# acts over the store, never dispatch commands.
OPERATOR_VERBS: Final = ("versions", "redact", "revert")


@dataclass(frozen=True, slots=True)
class Request:
    """A fully parsed invocation — the typed boundary after argparse.

    `model`/`min_age_days` are the maintain verb's; `limit`, `scope_wide`
    and `version` belong to the operator verbs; the six dispatch commands
    leave every extra field at its default."""

    settings: StoreSettings
    command: str
    arguments: dict[str, Any]
    json_output: bool
    model: AnyModel | None = None
    min_age_days: int = MAINTENANCE_MIN_AGE_DAYS
    limit: int = 50
    scope_wide: bool = False
    version: int | None = None


def build_parser(
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

    # The maintain verb (DESIGN §16) is not a dispatch command: it runs the
    # gardener over the writable mounts — keyless by default, --model adds
    # the semantic pass — so its --json envelope is its own (§14.2).
    maintain = command(
        "maintain",
        "consolidate the store: prune empty documents, merge duplicates"
        " (--model adds the semantic pass)",
    )
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
    subs["maintain"] = maintain

    # The operator verbs (§14.2): keyless audit and remedy over the store.
    versions = command("versions", "list a document's version rows, newest first")
    versions.add_argument("path", help="virtual document path")
    versions.add_argument(
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="most recent rows to show (default 50)",
    )

    redact = command(
        "redact", "clear a document's content everywhere, keeping the audit skeleton"
    )
    redact.add_argument("path", help="virtual document path (or a mount root + --all)")
    redact.add_argument(
        "--all",
        action="store_true",
        dest="scope_wide",
        help="required to redact a whole mount's scope (irreversible)",
    )

    revert = command("revert", "undo one version row (must be the newest)")
    revert.add_argument("path", help="virtual document path")
    revert.add_argument(
        "--version",
        type=int,
        required=True,
        metavar="N",
        help="the version row to undo (from `versions`)",
    )
    subs["versions"] = versions
    subs["redact"] = redact
    subs["revert"] = revert

    for sub in subs.values():
        add_store_arguments(sub, default_actor="cli:local")
        sub.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="print the result as JSON on stdout",
        )
    return parser, subs


def parse_request(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    stdin: TextIO,
    out: TextIO,
    err: TextIO,
    prog: str,
) -> Request:
    parser, subs = build_parser(prog, out, err)
    args = parser.parse_args(list(argv))
    command: str = args.command
    settings = resolve_store_settings(subs[command], args, env, layout=Path.cwd())
    if command == "maintain":
        if args.min_age_days < 0:
            subs[command].error("--min-age-days must not be negative")
        return Request(
            settings=settings,
            command=command,
            arguments={},
            json_output=args.json_output,
            model=parse_model(subs[command], args.model),
            min_age_days=args.min_age_days,
        )
    if command in OPERATOR_VERBS:
        return _parse_operator(subs[command], args, settings)
    arguments = {key: getattr(args, key) for key in ARGUMENT_KEYS[command]}
    for key in _STDIN_KEYS:
        if arguments.get(key) == "-":
            arguments[key] = stdin.read()
    return Request(
        settings=settings,
        command=command,
        arguments=arguments,
        json_output=args.json_output,
    )


def _parse_operator(sub: StreamParser, args: Any, settings: StoreSettings) -> Request:
    """The operator verbs' grammar tier — every guard a pure string or
    int check, so exit 2 still constructs nothing (§14.1)."""
    command: str = args.command
    request = Request(
        settings=settings,
        command=command,
        arguments={"path": args.path},
        json_output=args.json_output,
    )
    if command == "versions":
        if args.limit < 1:
            sub.error("--limit must be at least 1")
        return replace(request, limit=args.limit)
    if command == "redact":
        is_root = "/" not in args.path.strip("/")
        if is_root and not args.scope_wide:
            sub.error(
                f"{args.path!r} names a whole mount: pass --all to redact"
                " its entire scope (irreversible)"
            )
        if not is_root and args.scope_wide:
            sub.error(
                "--all redacts a whole mount: pass the mount root, not a document"
            )
        return replace(request, scope_wide=args.scope_wide)
    if args.version < 1:
        sub.error("--version must be at least 1 (versions start at 1)")
    return replace(request, version=args.version)


def parse_model(sub: StreamParser, value: str | None) -> AnyModel | None:
    """A model id from argv, `provider:model` accepted (the eval spelling);
    unknown ids are grammar-tier errors — nothing is constructed."""
    if value is None:
        return None
    bare = value.split(":", 1)[1] if ":" in value else value
    model = lookup_model(bare)
    if model is not None:
        return model
    known = ", ".join([m.value for m in Model] + [m.value for m in registered_models()])
    sub.error(f"unknown model {value!r} (known: {known})")
    raise AssertionError("unreachable")  # error() raises SystemExit
