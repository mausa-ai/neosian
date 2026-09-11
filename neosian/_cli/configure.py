"""`neosian configure` — the keys, non-interactive and catalog-driven
(DESIGN §30).

Forms: `--list` (the default under a pipe) names every provider the
table knows and where its key comes from — never the value; `--provider
NAME --key -` reads one key from stdin (a literal value is refused:
secrets never in argv); `--delete [--provider NAME]` drops one key or the
file; bare on a terminal prompts for each provider in turn. `--json` on
every form; exit 2 for an unknown provider or a malformed form, nothing
written.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Final, TextIO

from neosian._cli.config import (
    delete_api_key,
    delete_config,
    get_all_credentials,
    get_config_path,
    set_api_key,
)
from neosian._cli.providers import (
    ProviderKey,
    find_provider,
    key_source,
    provider_keys,
)
from neosian._foundation.memory.settings import StreamParser

_DESCRIPTION: Final = "Store a provider's API key under the home, or list them."
_EPILOG: Final = (
    "Keys live in <home>/config.toml (0600) and reach the library through "
    "the environment; an environment variable always wins over the file. "
    "A key is read from stdin (--key -), never from the command line."
)
_STDIN: Final = "-"
# A prompt: the label in, the typed value out ('' keeps the existing key).
Prompt = Callable[[str], str]


def _mask(key: str) -> str:
    return "****" if len(key) <= 7 else f"{key[:4]}****{key[-3:]}"


def _rows(env: Mapping[str, str]) -> list[dict[str, str | None]]:
    return [
        {"name": row.name, "env": row.env, "source": key_source(row, env)}
        for row in provider_keys()
    ]


def _list(env: Mapping[str, str], *, json_output: bool, out: TextIO) -> int:
    rows = _rows(env)
    if json_output:
        payload = {"config_path": str(get_config_path()), "providers": rows}
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 0
    width = max(len(str(row["name"])) for row in rows)
    for row in rows:
        source = row["source"] or "-"
        out.write(f"{str(row['name']).ljust(width)}  {row['env']}  {source}\n")
    out.write(f"config: {get_config_path()}\n")
    return 0


def _rich_prompt(out: TextIO) -> Prompt:
    from rich.console import Console
    from rich.prompt import Prompt as RichPrompt

    console = Console(file=out)

    def ask(label: str) -> str:
        return RichPrompt.ask(label, password=True, default="", console=console)

    return ask


def prompt_keys(prompt: Prompt, *, out: TextIO) -> int:
    """Every provider in turn; Enter keeps an existing key."""
    stored = get_all_credentials()
    out.write("Enter keeps an existing key; a key is stored under the home.\n")
    for row in provider_keys():
        existing = stored.get(row.key, "")
        label = f"{row.name} ({row.env})"
        if existing:
            label += f" [{_mask(existing)}]"
        value = prompt(label)
        if value:
            set_api_key(row.key, value)
            out.write(f"saved {row.name}\n")
    out.write(f"config: {get_config_path()}\n")
    return 0


def _emit(
    payload: dict[str, object], line: str, *, json_output: bool, out: TextIO
) -> int:
    if json_output:
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        out.write(line + "\n")
    return 0


def run_configure(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    stdin: TextIO,
    out: TextIO,
    err: TextIO,
    tty: bool,
    prompt: Prompt | None = None,
    prog: str = "neosian configure",
) -> int:
    """Parse and execute one form; write nothing on exit 2."""
    parser = StreamParser(prog=prog, description=_DESCRIPTION, epilog=_EPILOG)
    parser.bind(out, err)
    parser.add_argument("--provider", help="the provider (see --list)")
    parser.add_argument(
        "--key", help="'-' reads the key from stdin (the only accepted value)"
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="drop --provider's key, or the whole file without --provider",
    )
    parser.add_argument(
        "--list", action="store_true", dest="listing", help="every provider and source"
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="one JSON object"
    )
    try:
        args = parser.parse_args(list(argv))
        row: ProviderKey | None = None
        if args.provider is not None:
            row = find_provider(args.provider)
            if row is None:
                known = ", ".join(r.name for r in provider_keys())
                parser.error(f"unknown provider {args.provider!r}; known: {known}")
        if args.key is not None and args.key != _STDIN:
            parser.error("a key is read from stdin: pass --key - and pipe the value")
        if args.key is not None and row is None:
            parser.error("--key needs --provider")
        if row is not None and args.key is None and not args.delete:
            parser.error("--provider needs --key - or --delete")
    except SystemExit as exc:  # argparse: usage already on the streams
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2

    if args.delete:
        if row is not None:
            existed = delete_api_key(row.key)
            payload: dict[str, object] = {"deleted": row.name, "existed": existed}
            line = f"deleted {row.name}" if existed else f"no key for {row.name}"
            return _emit(payload, line, json_output=args.json_output, out=out)
        existed = delete_config()
        path = str(get_config_path())
        payload = {"deleted": path, "existed": existed}
        line = f"deleted {path}" if existed else f"no config at {path}"
        return _emit(payload, line, json_output=args.json_output, out=out)
    if row is not None:
        value = stdin.read().strip()
        if not value:
            err.write("error: empty key on stdin\n")
            return 1
        set_api_key(row.key, value)
        payload = {
            "saved": row.name,
            "env": row.env,
            "config_path": str(get_config_path()),
        }
        line = f"saved {row.name} ({row.env}) in {get_config_path()}"
        return _emit(payload, line, json_output=args.json_output, out=out)
    if args.listing or args.json_output or not tty:
        return _list(env, json_output=args.json_output, out=out)
    return prompt_keys(prompt if prompt is not None else _rich_prompt(out), out=out)
