"""neosian CLI entry point.

Provides the main CLI application with commands.
"""

import asyncio
import importlib.resources
import os
import sys
from importlib.util import find_spec
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from neosian import __version__
from neosian._cli.playground import run_playground
from neosian._cli.ui import BRAND_ACCENT
from neosian._foundation.shared.constants import App, Assets

# The help is grouped by audience (DESIGN §30): who each verb is for.
_TALK = "Talk"
_OPERATE = "Operate"
_CONNECT = "Connect an agent"
_LEARN = "Learn"

app = typer.Typer(
    name="neosian",
    help="The state layer for LLM agents: memory, conversations, the record.",
    epilog="agents: neosian docs cli --json",
    no_args_is_help=True,
    add_completion=False,
)


def _load_logo() -> str:
    """Load the small ASCII logo from assets."""
    try:
        files = importlib.resources.files(Assets.PACKAGE)
        logo_file = files.joinpath(Assets.LOGO_FILE)
        return logo_file.read_text(encoding="utf-8").rstrip()
    except Exception:
        return ""


@app.command(rich_help_panel=_TALK)
def chat(
    prompt: Annotated[
        str | None,
        typer.Argument(help="One turn: print the answer and exit (also: piped stdin)"),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="A model id, or `fake` to try it keyless"),
    ] = None,
    agent: Annotated[
        str | None,
        typer.Option("--agent", help="An agent file instead of the resident agent"),
    ] = None,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Resume a conversation by id"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="One-shot mode: the response envelope"),
    ] = False,
) -> None:
    """Talk to your memory: the resident agent that knows neosian.

    Bare on a terminal opens a session; a PROMPT (or piped stdin) runs one
    turn and prints the answer. The model is --model, else [chat] model in
    config.toml, else the first provider with a key (Anthropic, OpenAI,
    Cerebras, registered doors). Every turn persists under the home.

    Example:
        neosian chat
        neosian chat "what do you know about this project?"
        echo hi | neosian chat --model fake --json
    """
    from neosian._cli.chat_cmd import run_chat_command

    raise typer.Exit(
        run_chat_command(
            prompt, model=model, agent=agent, resume=resume, json_output=json_output
        )
    )


@app.command(rich_help_panel=_TALK)
def playground(
    agent_file: Annotated[
        str,
        typer.Argument(help="Path to the agent Python file"),
    ],
    menu: Annotated[
        bool,
        typer.Option(
            "--menu",
            help="Pick provider and model from a terminal menu (Unix terminals only)",
        ),
    ] = False,
    arena: Annotated[
        bool,
        typer.Option(
            "--arena",
            help="Arena mode: several models side by side (Unix terminals only)",
        ),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option(
            "--resume",
            help="Resume a conversation by id (see .neosian/conversations/)",
        ),
    ] = None,
) -> None:
    """Start an interactive playground session with an agent.

    The agent file must define:
        - system_prompt: str
        - tools: list[ToolFunction]

    Example:
        neosian playground my_agent.py
        neosian playground my_agent.py --menu
        neosian playground my_agent.py --arena
        neosian playground my_agent.py --resume 20260820-143207-my_agent

    The --menu and --arena pickers draw a terminal menu that needs a Unix
    terminal (termios); everything else runs anywhere.
    """
    run_playground(agent_file, menu=menu, arena=arena, resume=resume)


def _driver_line() -> str:
    """The one extra, by presence: the banner says what this install carries."""
    if find_spec("psycopg") is None:
        return 'psycopg (PostgresStore): missing\n  uv add "neosian[postgres]" adds it'
    return "psycopg (PostgresStore): installed"


@app.command(
    name="status",
    rich_help_panel=_OPERATE,
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
)
def status(ctx: typer.Context) -> None:
    """Is this machine set up? The home, the keys, the clients, the shape.

    A thin pass-through to the one grammar (`neosian status --help`);
    exit 0 whenever it ran — findings are data, `--json` one object.
    """
    from neosian.status import main as status_main

    raise typer.Exit(status_main(list(ctx.args)))


@app.command(
    name="configure",
    rich_help_panel=_OPERATE,
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
)
def configure(ctx: typer.Context) -> None:
    """Store a provider's API key under the home, or list them.

    A thin pass-through to the one grammar (`neosian configure --help`):
    `--list`, `--provider NAME --key -` (stdin), `--delete`, `--json`;
    bare on a terminal prompts for each provider in turn.
    """
    from neosian._cli.configure import run_configure

    raise typer.Exit(
        run_configure(
            list(ctx.args),
            os.environ,
            stdin=sys.stdin,
            out=sys.stdout,
            err=sys.stderr,
            tty=sys.stdin.isatty() and sys.stdout.isatty(),
        )
    )


@app.command(name="eval", rich_help_panel=_TALK)
def evaluate(
    config_file: Annotated[
        str,
        typer.Argument(help="Path to the evaluation config YAML file"),
    ],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the report as one JSON object on stdout"),
    ] = False,
) -> None:
    """Run an eval suite: variants × models × cases over one agent.

    Results are displayed in the terminal and saved to JSON. Exits
    nonzero when any case fails, so the command works as a CI gate;
    --json prints the saved artifact's document instead of the table.

    Example:
        neosian eval eval_suite.yaml
    """
    import json
    from datetime import UTC, datetime

    from neosian._cli.providers import load_keys_into_env
    from neosian._foundation.evaluation.reporter import report_dict
    from neosian.evaluation import (
        EvalProgress,
        create_progress_callback,
        load_eval_config,
        print_report,
        run_evaluation,
        save_report,
    )

    # The library reads keys from the environment only; loading them from the
    # CLI config file is the CLI's job, done here before the run.
    load_keys_into_env()

    console = Console(stderr=json_output)

    try:
        config = load_eval_config(config_file)
    except Exception as e:
        if json_output:
            print(json.dumps({"error": f"loading config: {e}", "hint": None}))
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1) from None

    progress = EvalProgress(config) if not json_output else None
    try:
        if progress is not None:
            progress.start()
        report = asyncio.run(
            run_evaluation(
                config,
                on_progress=(
                    create_progress_callback(progress) if progress is not None else None
                ),
            )
        )
        if progress is not None:
            progress.stop()
    except Exception as e:
        if progress is not None:
            progress.stop()
        if json_output:
            print(json.dumps({"error": f"evaluation failed: {e}", "hint": None}))
        console.print(f"[red]Evaluation failed: {e}[/red]")
        raise typer.Exit(1) from None

    output_path = save_report(report)
    if json_output:
        print(json.dumps(report_dict(report, now=datetime.now(UTC)), default=str))
        raise typer.Exit(1 if report.failed else 0)
    console.print()
    print_report(report, console)
    console.print()
    console.print(f"[dim]{output_path}[/dim]")
    console.print(f"{report.passed}/{report.total} passed")
    raise typer.Exit(1 if report.failed else 0)


@app.command(
    name="memory",
    rich_help_panel=_OPERATE,
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
)
def memory(ctx: typer.Context) -> None:
    """Read and write agent memory from the shell.

    A thin pass-through: every argument goes verbatim to the one grammar
    (`neosian memory --help`). The six commands ride the shared memory
    dispatcher; --json prints the memory tool's envelope.
    """
    from neosian.memory.cli import main as memory_main

    raise typer.Exit(memory_main(list(ctx.args)))


@app.command(
    name="audit",
    rich_help_panel=_OPERATE,
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
)
def audit(ctx: typer.Context) -> None:
    """What was done, by whom, when — a scope's ledger (DESIGN §20).

    A thin pass-through: every argument goes verbatim to the one grammar
    (`neosian audit --help`). Answers the same on a FileStore root,
    Postgres, or the state process (--url).
    """
    from neosian.ledger import main as audit_main

    raise typer.Exit(audit_main(list(ctx.args)))


_PASS_THROUGH = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
    "help_option_names": [],
}


@app.command(name="export", rich_help_panel=_OPERATE, context_settings=_PASS_THROUGH)
def export(ctx: typer.Context) -> None:
    """Write the store to DIR, whole — history included (DESIGN §26).

    A thin pass-through to the one grammar (`neosian export --help`); the
    archive is a FileStore root you can read, serve or import anywhere.
    """
    from neosian.mobility import main as mobility_main

    raise typer.Exit(mobility_main(["export", *ctx.args]))


@app.command(name="import", rich_help_panel=_OPERATE, context_settings=_PASS_THROUGH)
def import_(ctx: typer.Context) -> None:
    """Restore an export into the store, verbatim (DESIGN §26).

    A thin pass-through to the one grammar (`neosian import --help`);
    every unit must be empty in the store — nothing merges.
    """
    from neosian.mobility import main as mobility_main

    raise typer.Exit(mobility_main(["import", *ctx.args]))


@app.command(
    name="record",
    rich_help_panel=_CONNECT,
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
)
def record(ctx: typer.Context) -> None:
    """Record a foreign agent's session from its hooks (DESIGN §20.9).

    A thin pass-through: every argument goes verbatim to the one grammar
    (`neosian record --help`). Reads one hook payload on stdin per call;
    `neosian record install` renders or applies the hooks.
    """
    from neosian.record import main as record_main

    raise typer.Exit(record_main(list(ctx.args)))


@app.command(
    name="mcp",
    rich_help_panel=_CONNECT,
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
)
def mcp(ctx: typer.Context) -> None:
    """Serve neosian memory to MCP clients on stdio.

    A thin pass-through: every argument goes verbatim to the one grammar
    (`python -m neosian.mcp --help`).
    """
    from neosian.mcp.serve import main as mcp_main

    raise typer.Exit(mcp_main(list(ctx.args)))


@app.command(
    name="serve",
    rich_help_panel=_CONNECT,
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
)
def serve(ctx: typer.Context) -> None:
    """Serve memory and conversations over HTTP — the state process.

    A thin pass-through: every argument goes verbatim to the one grammar
    (`neosian serve --help`). Needs NEOSIAN_SERVE_TOKEN.
    """
    from neosian.server.serve import main as serve_main

    raise typer.Exit(serve_main(list(ctx.args)))


@app.command(rich_help_panel=_LEARN)
def version() -> None:
    """Display version information."""
    console = Console()

    # Load logo
    logo_text = _load_logo()

    # Build version info
    version_info = (
        f"\n"
        f"\n"
        f"  [bold {BRAND_ACCENT}]{App.NAME}[/bold {BRAND_ACCENT}]\n"
        f"  [dim]v{__version__}[/dim]\n"
        f"\n"
        f"  [dim]Python {App.PYTHON_VERSION}[/dim]\n"
        f"  [dim]{App.DESCRIPTION}[/dim]\n"
        f"\n"
        f"  [dim]in the box: Agent + Conversation,[/dim]\n"
        f"  [dim]the shell, MCP server and client,[/dim]\n"
        f"  [dim]the state process, OpenTelemetry;[/dim]\n"
        f"  [dim]{escape(_driver_line())}[/dim]\n"
        f"\n"
    )

    # Create table with logo on left, info on right
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="cyan", vertical="middle")
    table.add_column(vertical="middle")

    table.add_row(logo_text, version_info)

    console.print()
    console.print(table)


@app.command(rich_help_panel=_LEARN)
def docs(
    topic: Annotated[
        str | None,
        typer.Argument(help="Topic to print (omit to list the topics)"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print one JSON object on stdout"),
    ] = False,
) -> None:
    """Read the docs that ship in the wheel — version-true by construction.

    Example:
        neosian docs
        neosian docs topology
    """
    from neosian._cli.docs import run_docs

    raise typer.Exit(run_docs(topic, json_output=json_output))


def main() -> None:
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
