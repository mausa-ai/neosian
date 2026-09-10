"""`neosian chat` — the resident agent, one shot or a session (DESIGN
§30.2).

Model: `--model` (a shipped id, a registered door's id, `fake`), else
`[chat] model` in config.toml, else the first provider with a key in the
order Anthropic, OpenAI, Cerebras, registered doors; none exits 1 naming
`neosian configure`. A PROMPT argument or a non-terminal stdin runs one
turn and prints the answer — `--json` the response envelope — so an
agent or a script can use the resident agent; otherwise the playground's
loop opens on the same Conversation. Every turn persists under the home.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final, TextIO

from neosian._cli.chat import (
    chat_config,
    new_conversation_id,
    open_chat,
    resolve_resume,
)
from neosian._cli.chat_agent import RESIDENT_NAME, resident_config, with_chat_tools
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
from neosian._foundation.shared.types import DEFAULT_MODELS, AgentConfig, Provider

if TYPE_CHECKING:
    from neosian._foundation.agent.response import AgentResponse
    from neosian._foundation.shared.types import AnyModel

PROVIDER_ORDER: Final = (Provider.ANTHROPIC, Provider.OPENAI, Provider.CEREBRAS)
_NO_KEY: Final = (
    "no provider key found: run `neosian configure`, or pass --model fake "
    "to try the chat keyless"
)


class ChatUsageError(Exception):
    """A tier-2 miss: nothing constructed."""


class ChatError(Exception):
    """A tier-1 failure: the chat could not run."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def resolve_chat_model(flag: str | None, env: Mapping[str, str]) -> AnyModel:
    """The flag, else `[chat] model`, else the first keyed provider."""
    if flag is not None:
        model = lookup_model(flag)
        if model is None:
            raise ChatUsageError(f"unknown model {flag!r} (see `neosian docs agent`)")
        return model
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
) -> None:
    """One turn on a persisted Conversation; the answer on stdout. No
    reflection at close: a script's call is not a session boundary."""
    convo = open_chat(
        chat_config_of(config),
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


def chat_config_of(config: AgentConfig) -> AgentConfig:
    """The playground's derivation: memory on the home when the config
    names none (the resident agent already does)."""
    from neosian._foundation.memory.file import FileStore
    from neosian._foundation.memory.home import home

    return chat_config(config, FileStore(home()))


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
    """The verb's run tier: owns the loop, the streams and the tiers."""
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    load_keys_into_env()
    try:
        config, name = build_config(model, agent, os.environ)
        conversation_id = (
            resolve_resume(resume) if resume is not None else new_conversation_id(name)
        )
    except (ChatUsageError, ValueError, NeosianError) as exc:
        text = getattr(exc, "message", None) or str(exc)
        if json_output:
            out.write(json.dumps({"error": "usage", "hint": text}) + "\n")
        err.write(f"error: {text}\n")
        return 2
    except ChatError as exc:
        return _fail(exc.message, json_output=json_output, out=out, err=err)
    except Exception as exc:  # the agent file failed to load
        return _fail(str(exc), json_output=json_output, out=out, err=err)

    one_turn = prompt if prompt is not None else None
    if one_turn is None and not stdin.isatty():
        one_turn = stdin.read()
    if one_turn is not None:
        if not one_turn.strip():
            err.write("error: nothing to say — pass a PROMPT or pipe one on stdin\n")
            return 2
        try:
            asyncio.run(
                one_shot(
                    config,
                    one_turn.strip(),
                    conversation_id=conversation_id,
                    json_output=json_output,
                    out=out,
                )
            )
        except KeyboardInterrupt:
            return 130
        except Exception as exc:
            return _fail(str(exc), json_output=json_output, out=out, err=err)
        return 0

    from rich.console import Console

    from neosian._cli.chat import run_chat

    console = Console()
    try:
        asyncio.run(
            run_chat(
                console,
                config,
                name,
                conversation_id=conversation_id,
                resumed=resume is not None,
            )
        )
    except KeyboardInterrupt:
        console.print()
        return 130
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def _fail(message: str, *, json_output: bool, out: TextIO, err: TextIO) -> int:
    if json_output:
        out.write(json.dumps({"error": message, "hint": None}) + "\n")
    err.write(f"error: {message}\n")
    return 1
