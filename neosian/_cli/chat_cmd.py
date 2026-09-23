"""`neosian chat`: the resident agent, one shot or a session (DESIGN
§30.2). `run_conversation` is the run tier `neosian playground` shares
with it (§14.6).

Model: `--model` (a shipped id, a registered door's id, `fake`), else
`[chat] model` in config.toml, else the first provider with a key in the
order Anthropic, OpenAI, Cerebras, the shipped door rows (each door's
first model, enum order), registered doors; none exits 1 naming `neosian
configure`. A PROMPT argument or a non-terminal stdin runs one
turn and prints the answer — `--json` the response envelope — so an
agent or a script can use the resident agent; otherwise the session loop
opens on the same Conversation. Every turn persists under the home. The
`[[chat.mcp]]` tables of config.toml name MCP servers chat opens for the
session's lifetime, their tools added (`chat_mcp`; playground runs the
file as written).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final, TextIO

from rich.console import Console

from neosian._cli.chat import (
    new_conversation_id,
    open_chat,
    resolve_resume,
    run_chat,
)
from neosian._cli.chat_agent import RESIDENT_NAME, resident_config, with_chat_tools
from neosian._cli.chat_mcp import chat_servers, serving
from neosian._cli.config import get_section
from neosian._cli.providers import find_provider, load_keys_into_env
from neosian._foundation.conversation.reflection import ReflectionConfig
from neosian._foundation.llm.base import text_of
from neosian._foundation.shared.exceptions import NeosianError
from neosian._foundation.shared.registry import (
    lookup_model,
    registered_models,
    resolve_model,
)
from neosian._foundation.shared.types import (
    DEFAULT_MODELS,
    AgentConfig,
    Model,
    Provider,
)

if TYPE_CHECKING:
    from neosian._foundation.agent.response import AgentResponse
    from neosian._foundation.mcp.client import McpServer
    from neosian._foundation.shared.types import AnyModel

PROVIDER_ORDER: Final = (Provider.ANTHROPIC, Provider.OPENAI, Provider.CEREBRAS)
_NO_KEY: Final = (
    "no provider key found: run `neosian configure`, or pass --model fake "
    "to try the chat keyless"
)
_EMPTY_TURN: Final = "nothing to say: the turn is empty"
_JSON_NEEDS_A_TURN: Final = (
    "--json answers one turn: pipe it on stdin, or pass chat a PROMPT"
)


class ChatUsageError(Exception):
    """A tier-2 miss: nothing constructed."""


class ChatError(Exception):
    """A tier-1 failure: the chat could not run."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def model_from_flag(flag: str) -> AnyModel:
    """`--model`: a shipped id, a registered door's id or `fake`."""
    model = lookup_model(flag)
    if model is None:
        raise ChatUsageError(f"unknown model {flag!r} (see `neosian docs agent`)")
    return model


def resolve_chat_model(flag: str | None, env: Mapping[str, str]) -> AnyModel:
    """The flag, else `[chat] model`, else the first keyed provider."""
    if flag is not None:
        return model_from_flag(flag)
    configured = get_section("chat").get("model")
    if isinstance(configured, str):
        model = lookup_model(configured)
        if model is None:
            raise ChatUsageError(
                f"[chat] model = {configured!r} in config.toml is unknown"
            )
        return model
    for provider in PROVIDER_ORDER:
        row = find_provider(provider.value)
        if row is not None and env.get(row.env):
            return DEFAULT_MODELS[provider]
    for shipped in Model:  # a door's first row, enum order: xAI, Gemini, ...
        if shipped.door is not None and env.get(shipped.door.api_key_env):
            return shipped
    for registered in registered_models():
        if env.get(registered.door.api_key_env):
            return registered
    raise ChatError(_NO_KEY)


def build_config(
    model_flag: str | None, agent_file: str | None, env: Mapping[str, str]
) -> tuple[AgentConfig, str]:
    """The agent and its name: the resident agent, or an agent file with
    chat's tools added (the playground path)."""
    if agent_file is None:
        return resident_config(resolve_chat_model(model_flag, env)), RESIDENT_NAME
    from neosian._foundation.agent.loader import load_agent_config

    base, name = load_agent_config(agent_file)
    config = with_chat_tools(base)
    if model_flag is not None:
        config = replace(config, model=resolve_chat_model(model_flag, env))
    return config, name


def envelope(response: AgentResponse, conversation_id: str, model: AnyModel) -> str:
    usage = response.usage
    payload: dict[str, Any] = {
        "conversation_id": conversation_id,
        "model": model.value,
        "text": text_of(response.message),
        "tool_calls": [
            {"name": call.name, "arguments": call.arguments}
            for call in response.tool_calls_made
        ],
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
        },
        "cost_micro_usd": usage.cost_micro_usd(model),
    }
    return json.dumps(payload, ensure_ascii=False) + "\n"


async def one_shot(
    config: AgentConfig,
    text: str,
    *,
    conversation_id: str,
    json_output: bool,
    out: TextIO,
    servers: Sequence[McpServer] = (),
) -> None:
    """One turn on a persisted Conversation; the answer on stdout. No
    reflection at close: a script's call is not a session boundary."""
    async with serving(servers, config) as config:
        convo = open_chat(
            config,
            conversation_id=conversation_id,
            reflection=ReflectionConfig(enabled=False),
        )
        await convo.start()
        async with convo:
            response = await convo.send(text)
    if json_output:
        out.write(envelope(response, conversation_id, resolve_model(config.model)))
    else:
        out.write(text_of(response.message) + "\n")


def usage(message: str, *, json_output: bool, out: TextIO, err: TextIO) -> int:
    """Tier 2 (§14.1): the invocation was wrong; with `--json`, the one
    usage object as well (#213)."""
    if json_output:
        out.write(json.dumps({"error": "usage", "hint": message}) + "\n")
    err.write(f"error: {message}\n")
    return 2


def failed(message: str, *, json_output: bool, out: TextIO, err: TextIO) -> int:
    """Tier 1: the command ran and failed; with `--json`, one error object."""
    if json_output:
        out.write(json.dumps({"error": message, "hint": None}) + "\n")
    err.write(f"error: {message}\n")
    return 1


def run_chat_command(
    prompt: str | None,
    *,
    model: str | None,
    agent: str | None,
    resume: str | None,
    json_output: bool,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """The verb's run tier: the resident agent (or an agent file with
    chat's tools), then the tier chat and playground share."""
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    load_keys_into_env()
    try:  # grammar first: an agent file's own ValueError is tier 1 below
        servers = chat_servers(get_section("chat"))
    except ValueError as exc:
        return usage(str(exc), json_output=json_output, out=out, err=err)
    try:
        config, name = build_config(model, agent, os.environ)
    except ChatUsageError as exc:
        return usage(str(exc), json_output=json_output, out=out, err=err)
    except ChatError as exc:
        return failed(exc.message, json_output=json_output, out=out, err=err)
    except Exception as exc:  # the agent file failed to load
        return failed(str(exc), json_output=json_output, out=out, err=err)
    return run_conversation(
        config,
        name,
        prompt=prompt,
        resume=resume,
        json_output=json_output,
        stdin=stdin,
        out=out,
        err=err,
        servers=servers,
    )


def run_conversation(
    config: AgentConfig,
    name: str,
    *,
    prompt: str | None,
    resume: str | None,
    json_output: bool,
    stdin: TextIO,
    out: TextIO,
    err: TextIO,
    servers: Sequence[McpServer] = (),
) -> int:
    """The run tier chat and playground share (DESIGN §14.6): one turn
    from a PROMPT or a piped stdin prints the answer (`--json`: the
    envelope); a terminal opens the session loop. `--json` never opens a
    session: it promises one JSON object, which a session cannot keep.
    `servers` are open for the turn or the session (chat's tables)."""
    try:
        conversation_id = (
            resolve_resume(resume) if resume is not None else new_conversation_id(name)
        )
    except (ValueError, NeosianError) as exc:
        return usage(str(exc), json_output=json_output, out=out, err=err)
    turn = prompt
    if turn is None and not stdin.isatty():
        turn = stdin.read()
    if turn is None:
        if json_output:
            return usage(_JSON_NEEDS_A_TURN, json_output=True, out=out, err=err)
        console = Console()
        try:
            asyncio.run(
                run_chat(
                    console,
                    config,
                    name,
                    conversation_id=conversation_id,
                    resumed=resume is not None,
                    servers=servers,
                )
            )
        except KeyboardInterrupt:
            console.print()
            return 130
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 1
        except NeosianError as exc:  # a server refused before the session
            return failed(str(exc), json_output=json_output, out=out, err=err)
        return 0
    if not turn.strip():
        return usage(_EMPTY_TURN, json_output=json_output, out=out, err=err)
    try:
        asyncio.run(
            one_shot(
                config,
                turn.strip(),
                conversation_id=conversation_id,
                json_output=json_output,
                out=out,
                servers=servers,
            )
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        return failed(str(exc), json_output=json_output, out=out, err=err)
    return 0
