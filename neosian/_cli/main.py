"""neosian CLI entry point.

Provides the main CLI application with commands.
"""

import os
import sys
from typing import Annotated

import typer

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
    turn and prints the answer. The model is --model, else \\[chat] model in
    config.toml, else a model a local llama-server or Ollama is running
    (\\[chat] local adds base URLs), else the latest Sonnet; \\[\\[chat.mcp]]
    tables there are MCP servers chat opens for the session. Every turn
    persists under the home.

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
    model: Annotated[
        str | None,
        typer.Option(
            "--model", help="A model id instead of the file's (`fake`: keyless)"
        ),
    ] = None,
    menu: Annotated[
        bool,
        typer.Option("--menu", help="Pick the model from a menu (a terminal only)"),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Resume a conversation by id"),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="One-shot mode: the response envelope"),
    ] = False,
) -> None:
    """Try an agent file: a session on a terminal, one turn from a pipe.

    The file exports `configuration`, an AgentConfig, and runs as written
    on its own model unless --model or --menu picks another. Piped stdin
    runs one turn and prints the answer (--json: the envelope). Every turn
    persists under the home.

    Example:
        neosian playground my_agent.py
        neosian playground my_agent.py --menu
        echo hi | neosian playground my_agent.py --model fake --json
        neosian playground my_agent.py --resume 20260820-143207-my_agent
    """
    from neosian._cli.playground import run_playground

    raise typer.Exit(
        run_playground(
            agent_file, model=model, menu=menu, resume=resume, json_output=json_output
        )
    )


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
    from neosian._cli.render import render_status, run_rendered
    from neosian.status import main as status_main

    raise typer.Exit(
        run_rendered(
            status_main, list(ctx.args), render_status, out=sys.stdout, env=os.environ
        )
    )


@app.command(
    name="setup",
    rich_help_panel=_OPERATE,
    context_settings=_PASS_THROUGH,
)
def setup(ctx: typer.Context) -> None:
    """Wire the installed agents to this store: MCP and the hooks.

    Select clients on terminal writes; scripts use --yes or --client.
    A thin pass-through to the one grammar (`neosian setup --help`):
    prints what would land for every client found, once per machine,
    `--write` applies it, `--url` points them all at the state process,
    `--client C` narrows, `--json` one object.
    """
    from neosian._cli.shell_reports import setup as run_setup

    raise typer.Exit(run_setup(list(ctx.args)))


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
    from neosian._cli.shell_reports import configure as run_configure

    raise typer.Exit(run_configure(list(ctx.args)))


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
    output: Annotated[
        str | None,
        typer.Option(
            "--output", help="The artifact's directory (default .neosian/evals)"
        ),
    ] = None,
) -> None:
    """Run an eval suite: variants × models × cases over one agent.

    Results are displayed in the terminal and saved to JSON. Exits
    nonzero when any case fails, so the command works as a CI gate;
    --json prints the saved artifact's document instead of the table.
    The paths a suite names resolve beside the suite file; the progress
    tree is live on a terminal only.

    Example:
        neosian eval eval_suite.yaml
        neosian eval eval_suite.yaml --json --output reports/
    """
    from neosian._cli.eval_cmd import run_eval
    from neosian._cli.render import rendered

    raise typer.Exit(
        run_eval(
            config_file,
            json_output=json_output,
            output=output,
            live=rendered(sys.stdout, os.environ),
        )
    )


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
    from neosian._cli.shell_reports import memory as run_memory

    raise typer.Exit(run_memory(list(ctx.args)))


@app.command(
    name="search",
    rich_help_panel=_OPERATE,
    context_settings=_PASS_THROUGH,
)
def search(ctx: typer.Context) -> None:
    """Find the turns holding every term, newest first (DESIGN §32).

    A thin pass-through: every argument goes verbatim to the one grammar
    (`neosian search --help`). Store-wide, `--conversation` narrows;
    answers the same on a FileStore root, Postgres, or the state process.
    """
    from neosian._cli.render import render_search, run_rendered
    from neosian.search import main as search_main

    raise typer.Exit(
        run_rendered(
            search_main, list(ctx.args), render_search, out=sys.stdout, env=os.environ
        )
    )


@app.command(name="continue", rich_help_panel=_OPERATE, context_settings=_PASS_THROUGH)
def continue_(ctx: typer.Context) -> None:
    """Print a recorded conversation as continue_session delivers it (§33):
    a read (the note stays pending, no lineage written); every argument
    goes verbatim to the one grammar (`neosian continue --help`)."""
    from neosian.continuation import main as continue_main

    raise typer.Exit(continue_main(list(ctx.args)))


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
    from neosian._cli.render import render_audit, run_rendered
    from neosian.ledger import main as audit_main

    raise typer.Exit(
        run_rendered(
            audit_main, list(ctx.args), render_audit, out=sys.stdout, env=os.environ
        )
    )


@app.command(name="redact", rich_help_panel=_OPERATE, context_settings=_PASS_THROUGH)
def redact(ctx: typer.Context) -> None:
    """Blank recorded turns of one conversation, the skeleton kept (DESIGN §38).

    A thin pass-through to the one grammar (`neosian redact --help`);
    irreversible, so the whole conversation takes an explicit `--all`.
    """
    from neosian._cli.shell_reports import erasure as erasure_main

    raise typer.Exit(erasure_main(["redact", *ctx.args]))


@app.command(name="prune", rich_help_panel=_OPERATE, context_settings=_PASS_THROUGH)
def prune(ctx: typer.Context) -> None:
    """Redact every conversation older than a cutoff, sessions documents too.

    A thin pass-through to the one grammar (`neosian prune --help`);
    `--dry-run` reports the plan and writes nothing.
    """
    from neosian._cli.shell_reports import erasure as erasure_main

    raise typer.Exit(erasure_main(["prune", *ctx.args]))


@app.command(name="export", rich_help_panel=_OPERATE, context_settings=_PASS_THROUGH)
def export(ctx: typer.Context) -> None:
    """Write the store to DIR, whole — history included (DESIGN §26).

    A thin pass-through to the one grammar (`neosian export --help`); the
    archive is a FileStore root you can read, serve or import anywhere.
    """
    from neosian._cli.shell_reports import transfer as mobility_main

    raise typer.Exit(mobility_main(["export", *ctx.args]))


@app.command(name="import", rich_help_panel=_OPERATE, context_settings=_PASS_THROUGH)
def import_(ctx: typer.Context) -> None:
    """Restore an export into the store, verbatim (DESIGN §26).

    A thin pass-through to the one grammar (`neosian import --help`);
    every unit must be empty in the store — nothing merges.
    """
    from neosian._cli.shell_reports import transfer as mobility_main

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
    """Serve neosian memory to MCP clients on stdio; `install` registers it.

    A thin pass-through: every argument goes verbatim to the one grammar
    (`python -m neosian.mcp --help`); `neosian mcp install --client <c>`
    writes a client's registration.
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
    from neosian._cli.render import render_docs, run_rendered

    def engine(argv: list[str]) -> int:
        return run_docs(topic, json_output="--json" in argv)

    argv = ["--json"] if json_output else []
    raise typer.Exit(
        run_rendered(engine, argv, render_docs, out=sys.stdout, env=os.environ)
    )


@app.command(name="messages", rich_help_panel=_OPERATE, context_settings=_PASS_THROUGH)
def messages(ctx: typer.Context) -> None:
    """Send, read, acknowledge and reschedule durable messages."""
    from neosian._cli.messages import main

    raise typer.Exit(main(list(ctx.args)))


def main() -> None:
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
