"""The session's commands (DESIGN §35.4): a line that begins with `/`.

A command exists only where asking the agent is impossible or wrong: a
secret the model must never see, and the session itself (its model, its
conversation, its context). Everything else is a sentence to the agent,
which runs the shell's verbs (§35.2). Six here and `/exit`: seven, the
user's ruling (#327), and a new one has to pass the same test.
"""

from __future__ import annotations

import io
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from rich.console import Group
from rich.text import Text

from neosian._cli.chat import chat_config, new_conversation_id, resolve_resume
from neosian._cli.configure import run_configure
from neosian._cli.display import field_table
from neosian._cli.models import keyed_models
from neosian._cli.providers import key_source, provider_keys
from neosian._cli.tui.widgets import Working
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import home
from neosian._foundation.memory.sessions import (
    project_mount,
    session_ids,
    sessions_path,
)
from neosian._foundation.shared.registry import lookup_model
from neosian._foundation.shared.types import format_micro_usd

if TYPE_CHECKING:
    from neosian._cli.tui.app import SessionApp

_OWN_ID: Final = re.compile(r"\d{8}-\d{6}")  # the stamp a chat's own id begins with
_LAST_PROMPT: Final = re.compile(r"^- last prompt: (.*)$", re.MULTILINE)
_LISTED: Final = 20  # /resume offers this many of the newest
_KEYS: Final = (
    ("enter", "send"),
    ("ctrl+j", "a new line"),
    ("esc", "interrupt the turn: it is not saved"),
    ("ctrl+o", "expand or fold every tool call (a click does one)"),
    ("ctrl+c", "copy a selection, interrupt, clear the prompt; twice to leave"),
    ("pgup pgdn", "scroll"),
)
_EXIT: Final = "leave (ctrl+d too)"
_ASK: Final = "Anything else, ask in words: the agent runs the shell's verbs itself."
_NOTHING_SAVED: Final = "no key entered: nothing was saved"
_NO_KEYS: Final = "no provider has a key yet: /configure stores one"
_NO_SESSIONS: Final = "no earlier session of this chat in this project"
_NOTHING_TO_FOLD: Final = "nothing to fold yet: the recent turns stay whole"


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    summary: str
    run: Callable[[SessionApp, str], Awaitable[None]]


async def _help(app: SessionApp, _: str) -> None:
    rows = [(f"/{command.name}", command.summary) for command in COMMANDS]
    rows.append(("/exit", _EXIT))
    await app.say(
        Group(field_table(rows), Text(), field_table(_KEYS), Text(), Text(_ASK))
    )


async def _configure(app: SessionApp, _: str) -> None:
    """The one way a key enters from the session: picked, typed masked,
    piped to `configure --key -`. It is never mounted, sent or stored in
    a turn."""
    rows = provider_keys()
    labels = [
        f"{row.name}  ({row.env})"
        + ("  has a key" if key_source(row, os.environ) else "")
        for row in rows
    ]
    choice = await app.pick("Whose API key?", labels)
    if choice is None:
        return
    row = rows[choice]
    key = await app.secret(f"The {row.name} key ({row.env})")
    if key is None:
        await app.say(Text(_NOTHING_SAVED, style="dim"))
        return
    out, err = io.StringIO(), io.StringIO()
    argv = ["--provider", row.name, "--key", "-"]
    code = run_configure(
        argv, os.environ, stdin=io.StringIO(key), out=out, err=err, tty=False
    )
    if code != 0:
        raise RuntimeError(err.getvalue().strip() or f"configure exited {code}")
    os.environ[row.env] = key  # this process's next client reads it here
    await app.session.convo.aclose()  # the next send opens a fresh client pool
    await app.say(Text(out.getvalue().strip()))


async def _model(app: SessionApp, argument: str) -> None:
    if argument:
        model = lookup_model(argument)
        if model is None:
            raise ValueError(f"unknown model {argument!r}")
    else:
        models = keyed_models(os.environ)
        if not models:
            await app.say(Text(_NO_KEYS, style="dim"))
            return
        choice = await app.pick("Which model?", [label for _, label in models])
        if choice is None:
            return
        model = models[choice][0]
    before = app.session.title.plain
    await app.reopen(model=model)
    await app.say(Text(f"model: {before} → {app.session.title.plain}"))


async def _own_sessions(app: SessionApp) -> list[tuple[str, str]]:
    """This chat's earlier conversations in the project, newest first,
    each with its last prompt. Another agent's session is not offered: it
    is continued with `continue_session`, never appended to."""
    memory = chat_config(app.config, FileStore(home())).memory
    mount = project_mount(memory)
    if memory is None or mount is None:
        return []
    current = app.session.convo.conversation_id
    listed = await session_ids(memory.store, mount.scope)
    own = sorted((i for i in listed if _OWN_ID.match(i) and i != current), reverse=True)
    found = []
    for session_id in own[:_LISTED]:
        document = await memory.store.read(mount.scope, sessions_path(session_id))
        prompt = _LAST_PROMPT.search(document.content) if document else None
        found.append((session_id, prompt.group(1) if prompt else ""))
    return found


async def _resume(app: SessionApp, argument: str) -> None:
    if argument:
        target = resolve_resume(argument)
    else:
        sessions = await _own_sessions(app)
        if not sessions:
            await app.say(Text(_NO_SESSIONS, style="dim"))
            return
        labels = [f"{session_id}  {prompt}".rstrip() for session_id, prompt in sessions]
        choice = await app.pick("Which session?", labels)
        if choice is None:
            return
        target = sessions[choice][0]
    await app.reopen(conversation_id=target)


async def _new(app: SessionApp, _: str) -> None:
    await app.reopen(conversation_id=new_conversation_id(app.agent), resumed=False)


async def _compact(app: SessionApp, _: str) -> None:
    working = app.query_one(Working)
    working.start()
    try:
        result = await app.session.convo.compact()
    finally:
        working.stop()
    if not result.entries:
        await app.say(Text(_NOTHING_TO_FOLD, style="dim"))
        return
    said = Text(f"folded {len(result.entries)} turns into the log")
    model = lookup_model(result.model) if result.model else None
    if result.usage is not None and model is not None:
        cost = result.usage.cost_micro_usd(model)
        if cost is not None:
            said.append(f"  {format_micro_usd(cost)}", style="dim")
    await app.say(said)


COMMANDS: Final = (
    Command("help", "the commands and the keys", _help),
    Command(
        "configure", "enter an API key, masked: the model never sees it", _configure
    ),
    Command(
        "model", "switch the model, same conversation (/model ID names it)", _model
    ),
    Command("resume", "continue an earlier session of this chat (/resume ID)", _resume),
    Command("new", "start a fresh conversation", _new),
    Command("compact", "fold the older turns now", _compact),
)
BY_NAME: Final = {command.name: command for command in COMMANDS}
NAMES: Final = (*BY_NAME, "exit")
