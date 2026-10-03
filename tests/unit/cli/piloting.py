"""Drive the session headless (NY2): the app over a Conversation that has
not started, and `run_chat` under Textual's autopilot in place of a
terminal (the script leaves the app itself, `ctrl+d`)."""

import io
from collections.abc import Awaitable, Callable

import pytest
from rich.console import Console
from textual.pilot import Pilot

from neosian import AgentConfig
from neosian._cli.chat import Session, open_chat, opening, streams, turn_title
from neosian._cli.tui.app import SessionApp
from neosian._cli.tui.widgets import Prompt
from neosian._foundation.shared.registry import resolve_model

Script = Callable[[Pilot[None]], Awaitable[None]]


def session_app(
    config: AgentConfig,
    conversation_id: str = "t1",
    *,
    streamed: bool | None = None,
    agent: str = "probe",
) -> SessionApp:
    """`run_chat`'s app, minus the start: a first send starts the store."""
    convo = open_chat(config, conversation_id=conversation_id)
    session = Session(
        convo,
        opening(config, agent, convo, resumed=False),
        resolve_model(config.model),
        turn_title(config),
        streams(config) if streamed is None else streamed,
    )
    return SessionApp(session, config=config, agent=agent)


def plain(renderable: object) -> str:
    out = io.StringIO()
    Console(file=out, width=200, no_color=True).print(renderable)
    return out.getvalue()


async def send(pilot: Pilot[None], text: str) -> None:
    pilot.app.query_one(Prompt).text = text
    await pilot.press("enter")
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def piloted(monkeypatch: pytest.MonkeyPatch, script: Script) -> None:
    run_async = SessionApp.run_async

    async def headless(app: SessionApp) -> None:
        async def auto_pilot(pilot: Pilot[object]) -> None:
            await script(pilot)  # type: ignore[arg-type]

        await run_async(app, headless=True, auto_pilot=auto_pilot)

    monkeypatch.setattr(SessionApp, "run_async", headless)
