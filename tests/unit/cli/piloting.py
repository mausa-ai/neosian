"""Drive `run_chat`'s session headless: Textual's autopilot in place of a
terminal (NY2). The script leaves the app itself (`ctrl+d`)."""

import io
from collections.abc import Awaitable, Callable

import pytest
from rich.console import Console
from textual.pilot import Pilot

from neosian._cli.tui.app import SessionApp
from neosian._cli.tui.widgets import Prompt

Script = Callable[[Pilot[None]], Awaitable[None]]


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
