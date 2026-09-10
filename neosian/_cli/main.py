"""neosian CLI entry point.

Provides the main CLI application with commands.
"""

import os
import sys
from typing import Annotated

import typer

from neosian._cli.playground import run_playground

# The operator and agent verbs are verbatim pass-throughs to their own
# argparse grammars (§14.2's shape): every argument, --help included.
_PASS_THROUGH = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
    "help_option_names": [],
}
# The help is grouped by audience (DESIGN §30): who each verb is for.
_TALK = "Talk"
_OPERATE = "Operate"
_CONNECT = "Connect an agent"
_LEARN = "Learn"

app = typer.Typer(
    name="neosian",
    help="The state layer for LLM agents: memory, conversations, the record.",
    epilog="agents: neosian docs cli --json",
    invoke_without_command=True,
    add_completion=False,
)


def _on_a_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


@app.callback()
def root(ctx: typer.Context) -> None:
    """Bare `neosian` (DESIGN §30): on a terminal it opens `neosian chat`
    — the `claude` shape; under a pipe it prints this help, as before."""
    from neosian._cli.update import check_on_the_human_door, human_door

    if human_door(ctx.invoked_subcommand, on_terminal=_on_a_terminal(), argv=sys.argv):
        check_on_the_human_door(os.environ, err=sys.stderr)  # the knob, §30.3
    if ctx.invoked_subcommand is not None:
        return
    if _on_a_terminal():
        from neosian._cli.chat_cmd import run_chat_command

        raise typer.Exit(
            run_chat_command(
                None, model=None, agent=None, resume=None, json_output=False
            )
        )
    typer.echo(ctx.get_help())


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


@app.command(
    name="status",
    rich_help_panel=_OPERATE,
    context_settings=_PASS_THROUGH,
)
def status(ctx: typer.Context) -> None:
    """Is this machine set up? The home, the keys, the clients, the shape.

    A thin pass-through to the one grammar (`neosian status --help`);
    exit 0 whenever it ran — findings are data, `--json` one object.
    """
    from neosian.status import main as status_main

    raise typer.Exit(status_main(list(ctx.args)))


@app.command(
    name="setup",
    rich_help_panel=_OPERATE,
    context_settings=_PASS_THROUGH,
)
def setup(ctx: typer.Context) -> None:
    """Wire the installed agents to this store: MCP and the hooks.

    A thin pass-through to the one grammar (`neosian setup --help`):
    prints what would land for every client found, `--write` applies it,
    `--client C` narrows, `--json` one object.
    """
    from pathlib import Path

    from neosian._cli.setup import run_setup
    from neosian._foundation.shared.client_config import Environment

    raise typer.Exit(
        run_setup(
            list(ctx.args),
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
        )
    )


@app.command(name="update", rich_help_panel=_OPERATE, context_settings=_PASS_THROUGH)
def update(ctx: typer.Context) -> None:
    """Check PyPI for a newer neosian; print the command, or apply it.

    A thin pass-through to the one grammar (`neosian update --help`):
    `--check` (the default), `--write` (fenced), `--json`, `--mode M` sets
    the knob — off, notify or auto.
    """
    from neosian._cli.update import run_update

    raise typer.Exit(
        run_update(list(ctx.args), os.environ, out=sys.stdout, err=sys.stderr)
    )


@app.command(
    name="configure",
    rich_help_panel=_OPERATE,
    context_settings=_PASS_THROUGH,
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
    from neosian._cli.eval_cmd import run_eval

    raise typer.Exit(run_eval(config_file, json_output=json_output))


@app.command(
    name="memory",
    rich_help_panel=_OPERATE,
    context_settings=_PASS_THROUGH,
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
    context_settings=_PASS_THROUGH,
)
def audit(ctx: typer.Context) -> None:
    """What was done, by whom, when — a scope's ledger (DESIGN §20).

    A thin pass-through: every argument goes verbatim to the one grammar
    (`neosian audit --help`). Answers the same on a FileStore root,
    Postgres, or the state process (--url).
    """
    from neosian.ledger import main as audit_main

    raise typer.Exit(audit_main(list(ctx.args)))


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
    context_settings=_PASS_THROUGH,
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
    context_settings=_PASS_THROUGH,
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
    context_settings=_PASS_THROUGH,
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
    from neosian._cli.version import print_banner

    print_banner()


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
