"""Shell adapters around stream-injected engines; no execution in renderers."""

import io
import os
import sys
from pathlib import Path

from neosian._cli.render import Render, render_index, run_rendered
from neosian._cli.render_reports import (
    render_configure,
    render_maintenance,
    render_setup,
    render_transfer,
    render_versions,
)


def setup(argv: list[str]) -> int:
    from neosian._cli.setup import run_setup
    from neosian._foundation.shared.client_config import Environment

    def engine(args: list[str]) -> int:
        return run_setup(
            args,
            os.environ,
            context=Environment(
                home=Path.home(),
                cwd=Path.cwd(),
                platform=sys.platform,
                env=os.environ,
                executable=sys.executable,
            ),
            out=sys.stdout,
            err=sys.stderr,
            stdin=sys.stdin,
            tty=sys.stdin.isatty() and sys.stdout.isatty(),
        )

    # Interactive selection must happen before JSON report capture, which
    # deliberately makes the engine noninteractive.
    if "--write" in argv and not any(
        arg in ("--yes", "--json", "--help", "-h", "--client")
        or arg.startswith("--client=")
        for arg in argv
    ):
        return engine(argv)
    return run_rendered(
        engine, argv, render_setup, out=sys.stdout, env=os.environ, partial=True
    )


def configure(argv: list[str]) -> int:
    from neosian._cli.configure import run_configure

    def engine(args: list[str]) -> int:
        return run_configure(
            args,
            os.environ,
            stdin=sys.stdin,
            out=sys.stdout,
            err=sys.stderr,
            tty=sys.stdin.isatty() and sys.stdout.isatty(),
        )

    # Interactive prompts and write receipts keep their direct stream.
    if "--list" not in argv:
        return engine(argv)
    return run_rendered(engine, argv, render_configure, out=sys.stdout, env=os.environ)


def memory(argv: list[str]) -> int:
    from neosian._foundation.memory.cli_grammar import build_parser
    from neosian.memory.cli import main as engine

    # The same argparse grammar resolves the path even with interleaved flags.
    # This parse never reads stdin or opens a store; errors/help belong to engine.
    parser, _ = build_parser("neosian memory", io.StringIO(), io.StringIO())
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return engine(argv)
    render: Render | None = {
        "versions": render_versions,
        "maintain": render_maintenance,
    }.get(args.command)
    if args.command == "view" and not args.path.strip("/"):
        render = render_index
    if render is None:
        return engine(argv)
    return run_rendered(
        engine,
        argv,
        render,
        out=sys.stdout,
        env=os.environ,
        partial=args.command == "maintain",
    )


def transfer(argv: list[str]) -> int:
    from neosian.mobility import main as engine

    return run_rendered(engine, argv, render_transfer, out=sys.stdout, env=os.environ)
