"""The resident agent's shell tool (DESIGN §35.2): one tool over the
`neosian` verbs, run as the literal shell on this interpreter.

The agent passes the argv after `neosian`; the tool spawns the shell with
`--json` and returns the envelope. A form that only reads runs at once.
Every other form waits for the human: `Consent` is the §17 approver the
resident agent carries, and it asks whoever the session registered (the
app's modal); with no one to ask (a one-shot turn, a pipe) it declines
in-band, naming the command for the user to run. A session, a server and
the hook door do not run inside a chat, and a key never passes through
the model: those forms are refused by the tool itself, gate or no gate.

No `from __future__ import annotations`: @Tool resolves the signature's
annotations at decoration time.
"""

import asyncio
import json
import shlex
import sys
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Final

from neosian._foundation.agent.approval import ToolApprovalRequest, ToolDecision
from neosian._foundation.shared.prompt_assets import get_prompt, get_prompt_params
from neosian._foundation.shared.types import ToolFunction
from neosian._foundation.tools.base import Tool, ToolResult

NAME: Final = "neosian"
_ENTRY: Final = "neosian._cli.entry"
_TIMEOUT_SECONDS: Final = 300.0
_HELP: Final = frozenset({"--help", "-h"})
_AS_TYPED: Final = _HELP | {"--json", "--"}  # the argv gets no --json of ours

# Every form of these only reads.
_READ_VERBS: Final = frozenset(
    {"status", "docs", "version", "search", "audit", "continue"}
)
# The commands of these that only read; the command is the first argument.
_READ_COMMANDS: Final = {
    "memory": frozenset({"view", "versions"}),
    "messages": frozenset({"list", "view"}),
}
# The rest of what the tool runs: a form of these asks unless `reads` says
# otherwise (`update` asks whole: it contacts PyPI, #214).
_ASK_VERBS: Final = frozenset({"setup", "configure", "update", "export", "import"})
_VERBS: Final = _READ_VERBS | _READ_COMMANDS.keys() | _ASK_VERBS
# A session, a paid run, a server, the hook door: never inside a chat.
_OUTSIDE: Final = frozenset({"chat", "playground", "eval", "mcp", "record", "serve"})
_NO_JSON: Final = frozenset({"version"})

_NO_VERB: Final = "name a verb: args is the argv after `neosian`, e.g. ['status']"
_OUTSIDE_CHAT: Final = (
    "`neosian {verb}` does not run inside the chat (a session, a server or a "
    "hook door): tell the user the command instead"
)
_UNKNOWN: Final = "unknown verb {verb!r}; the verbs this tool runs: {verbs}"
_KEY: Final = (
    "an API key never passes through the chat: ask the user to run "
    "`neosian configure` themselves"
)
_NO_ONE_TO_ASK: Final = (
    "`{command}` changes state and needs the user's yes, which only an "
    "interactive session can give: tell the user the command to run"
)
_DECLINED: Final = "the user declined it"
_TIMED_OUT: Final = "`{command}` did not finish in {seconds:.0f}s and was stopped"

Ask = Callable[[str], Awaitable[bool]]


def command(args: Sequence[str]) -> str:
    return shlex.join((NAME, *args))


def refusal(args: Sequence[str]) -> str | None:
    """Why this form never runs from the chat, or None when it may."""
    if not args:
        return _NO_VERB
    verb = args[0]
    if verb in _OUTSIDE:
        return _OUTSIDE_CHAT.format(verb=verb)
    if verb not in _VERBS:
        return _UNKNOWN.format(verb=verb, verbs=", ".join(sorted(_VERBS)))
    # `--k` too: argparse takes an unambiguous prefix for the flag.
    if verb == "configure" and any(arg.startswith("--k") for arg in args[1:]):
        return _KEY
    return None


def reads(args: Sequence[str]) -> bool:
    """Whether the form only reads. Whatever is not known to, asks."""
    verb, rest = args[0], args[1:]
    if verb in _READ_VERBS or _HELP & set(rest):
        return True
    if verb in _READ_COMMANDS:
        return bool(rest) and rest[0] in _READ_COMMANDS[verb]
    if verb == "setup":  # `--w`: a prefix of --write is --write to argparse
        return not any(arg.startswith("--w") for arg in rest)
    if verb == "configure":
        return set(rest) <= {"--list", "--json"}
    return False


class Consent:
    """The resident agent's approver (§17): a read runs, a write asks.

    `ask` is the session's question to the human, set once there is one;
    until then a write is declined, default-deny like the gate itself.
    Every other tool passes: the gate is here for the shell tool alone.
    """

    def __init__(self) -> None:
        self.ask: Ask | None = None

    async def __call__(self, request: ToolApprovalRequest) -> ToolDecision:
        args = request.arguments.get("args")
        if (
            request.name != NAME
            or not isinstance(args, list)
            or not all(isinstance(arg, str) for arg in args)
            or refusal(args) is not None  # the tool refuses in its own words
            or reads(args)
        ):
            return ToolDecision(approved=True)
        if self.ask is None:
            return ToolDecision(False, _NO_ONE_TO_ASK.format(command=command(args)))
        if await self.ask(command(args)):
            return ToolDecision(approved=True)
        return ToolDecision(False, _DECLINED)


async def run(args: Sequence[str]) -> ToolResult[Any]:
    """Spawn the shell; exit 0 answers with the envelope, parsed."""
    argv = list(args)
    if args[0] not in _NO_JSON and not _AS_TYPED & set(argv):
        argv.append("--json")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        _ENTRY,
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), _TIMEOUT_SECONDS)
    except TimeoutError:
        return ToolResult.fail(
            _TIMED_OUT.format(command=command(args), seconds=_TIMEOUT_SECONDS)
        )
    finally:  # a timeout, or the turn interrupted: the child goes with it
        if process.returncode is None:
            process.kill()
            await process.wait()
    stdout = out.decode(errors="replace").strip()
    if process.returncode != 0:
        said = "\n".join(filter(None, (err.decode(errors="replace").strip(), stdout)))
        return ToolResult.fail(f"exit {process.returncode}: {said}")
    try:
        return ToolResult.ok(json.loads(stdout))
    except ValueError:
        return ToolResult.ok(stdout)


def create_shell_tool() -> ToolFunction:
    """`neosian(args)`: one verb of the shell, as the user would type it."""

    @Tool(
        name=NAME,
        description=get_prompt("tools.neosian"),
        params=get_prompt_params("tools.neosian_params"),
    )
    async def neosian(args: list[str]) -> ToolResult[Any]:
        """Run one verb of the neosian shell."""
        why = refusal(args)
        if why is not None:
            return ToolResult.fail(why)
        return await run(args)

    return neosian
