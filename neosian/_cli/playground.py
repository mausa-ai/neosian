"""`neosian playground`: an agent file under chat's run tier (DESIGN §14.6).

The file runs as written, its tools and its prompt; chat's `docs` tool
belongs to the resident agent and is never added here. The model is
`--model`, or `--menu` (the picker a terminal offers over the same
choice), else the file's own. Piped stdin runs one turn and prints the
answer (`--json`: the envelope); a terminal opens the session loop. Every
turn persists under the home (DESIGN §22).
"""

from __future__ import annotations

import dataclasses
import sys
from typing import Final, TextIO

from neosian._cli.chat_cmd import (
    ChatUsageError,
    failed,
    model_from_flag,
    run_conversation,
    usage,
)
from neosian._cli.providers import load_keys_into_env
from neosian._foundation.agent.loader import load_agent_config
from neosian._foundation.shared.types import AgentConfig, AnyModel

_MENU_AND_MODEL: Final = "--menu and --model are exclusive: the menu picks the model"
_MENU_NEEDS_A_TERMINAL: Final = "--menu needs a terminal: pass --model instead"
_CANCELLED: Final = "cancelled: nothing ran"


def with_model(config: AgentConfig, model: AnyModel) -> AgentConfig:
    """The model override, from `--model` or `--menu`: `replace()` carries
    every other field (the hand-rolled rebuild it replaced dropped nine)."""
    return dataclasses.replace(config, model=model)


def run_playground(
    agent_path: str,
    *,
    model: str | None,
    menu: bool,
    resume: str | None,
    json_output: bool,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """The verb's run tier: the grammar, the file, the model, then the
    tier chat and playground share."""
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    if menu and model is not None:
        return usage(_MENU_AND_MODEL, json_output=json_output, out=out, err=err)
    if menu and not stdin.isatty():
        return usage(_MENU_NEEDS_A_TERMINAL, json_output=json_output, out=out, err=err)
    load_keys_into_env()  # before the file: a door it registers finds its key
    try:
        config, name = load_agent_config(agent_path)
    except Exception as exc:
        return failed(str(exc), json_output=json_output, out=out, err=err)
    chosen: AnyModel | None = None
    if model is not None:  # after the file: it may register the door named
        try:
            chosen = model_from_flag(model)
        except ChatUsageError as exc:
            return usage(str(exc), json_output=json_output, out=out, err=err)
    elif menu:
        from rich.console import Console

        from neosian._cli.models import select_provider_and_model

        chosen = select_provider_and_model(
            Console(), require_reasoning=config.reasoning_effort is not None
        )
        if chosen is None:
            err.write(f"{_CANCELLED}\n")
            return 0
    if chosen is not None:
        config = with_model(config, chosen)
    return run_conversation(
        config,
        name,
        prompt=None,
        resume=resume,
        json_output=json_output,
        stdin=stdin,
        out=out,
        err=err,
    )
