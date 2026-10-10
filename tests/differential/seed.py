"""The seeded home both doors read (DESIGN §39.4): built by the Python
side alone, deterministic in content (one seeded generator) and real in
shape, through the doors a real home is written by: memory documents with
versions, a redaction, a rename and a deletion, two skills, sessions
recorded through the record verb for two clients, a handoff note and a
message. `SMALL` seeds the corpus; `REFERENCE` the latency gate, the
maintainer's home's shape (about fifty conversations and fifty
megabytes)."""

from __future__ import annotations

import io
import json
import os
import random
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.home import project_scope, user_scope
from neosian._foundation.memory.sessions import HANDOFF_PATH, handoff_document
from neosian._foundation.record.cli import run as record
from tests.unit.record.payloads import prompt, stop, tool

PROJECT_NAME: Final = "demo"
_SEED: Final = 2026
_WORDS: Final = [
    "the",
    "deploy",
    "target",
    "is",
    "eu-west-1",
    "and",
    "the",
    "release",
    "train",
    "leaves",
    "on",
    "tuesday",
    "every",
    "build",
    "id",
    "lives",
    "in",
    "the",
    "log",
    "the",
    "cache",
    "ttl",
    "is",
    "eight",
    "hours",
    "the",
    "catalog",
    "service",
    "answers",
    "in",
    "milliseconds",
    "while",
    "the",
    "ledger",
    "keeps",
    "every",
    "write",
]


@dataclass(frozen=True)
class Shape:
    conversations: int
    turns: int
    result_chars: int


SMALL: Final = Shape(conversations=3, turns=3, result_chars=400)
REFERENCE: Final = Shape(conversations=50, turns=4, result_chars=250_000)


@dataclass(frozen=True)
class Seeded:
    home: Path
    user: str
    proj: str
    sessions: tuple[str, ...]

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self.sessions)


def _text(rng: random.Random, chars: int) -> str:
    words: list[str] = []
    length = 0
    while length < chars:
        word = rng.choice(_WORDS)
        words.append(word)
        length += len(word) + 1
    return " ".join(words)


async def _record(
    home: Path, project: Path, payload: dict[str, Any], *, agent: str
) -> None:
    argv = [
        "--root",
        str(home),
        "--spool",
        str(home / "spool"),
        "--project",
        str(project),
    ]
    if agent != "claude-code":
        argv += ["--agent", agent]
    out, err = io.StringIO(), io.StringIO()
    code = await record(
        argv, {}, stdin=io.StringIO(json.dumps(payload)), out=out, err=err
    )
    if code != 0:
        raise RuntimeError(
            f"seeding {agent} {payload['hook_event_name']}: {err.getvalue()}"
        )


async def seed(home: Path, shape: Shape, *, console: Path) -> Seeded:
    """Build the home at `home` (created); `console` is the Python shell,
    for the one verb with no in-process seam (messages)."""
    rng = random.Random(_SEED)
    project = home.parent / PROJECT_NAME
    project.mkdir(exist_ok=True)
    user, proj = str(user_scope()), str(project_scope(project))
    store = FileStore(home)
    for version in range(3):
        await store.write(
            user,
            "profile",
            f"# Profile\n\nversion {version}: {_text(rng, 120)}\n",
            actor="seed",
        )
    await store.write(
        user,
        "preferences",
        "# Preferences\n\n- terse answers\n- no em dashes\n",
        actor="seed",
    )
    await store.write(
        user, "notes/reading", f"# Reading\n\n{_text(rng, 200)}\n", actor="seed"
    )
    for version in range(2):
        await store.write(
            proj,
            "facts",
            f"# Facts\n\n- deploy target: eu-west-1 (v{version})\n",
            actor="seed",
        )
    await store.write(
        proj,
        "decisions",
        "# Decisions\n\n- 2026-10-01: Postgres stays the second substrate\n",
        actor="seed",
    )
    await store.write(
        proj,
        "skills/release",
        "---\ndescription: Release this project: bump, test, tag.\n---\n\n# Release\n\n1. Bump\n2. `make test`\n3. Tag\n",
        actor="seed",
    )
    await store.write(
        proj,
        "skills/deploy",
        "---\ndescription: Deploy to the eu-west-1 target.\n---\n\n# Deploy\n\nRun the deploy script.\n",
        actor="seed",
    )
    await store.write(proj, "secret", "# Secret\n\ntoken: hunter2\n", actor="seed")
    await store.redact(proj, path="secret", actor="seed")
    await store.write(
        proj, "old-name", "# Moved\n\nthis document was renamed\n", actor="seed"
    )
    await store.rename(proj, "old-name", "new-name", actor="seed")
    await store.write(proj, "scratch", "# Scratch\n\ngone soon\n", actor="seed")
    await store.delete(proj, "scratch", actor="seed")

    sessions: list[str] = []
    for index in range(shape.conversations):
        session = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        sessions.append(session)
        agent = "codex" if index % 3 == 2 else "claude-code"
        for turn in range(shape.turns):
            await _record(
                home,
                project,
                prompt(f"turn {turn + 1}: {_text(rng, 60)}", session=session),
                agent=agent,
            )
            await _record(
                home,
                project,
                tool(
                    "Read",
                    tool_input={"file_path": f"/work/{PROJECT_NAME}/file{turn}.py"},
                    response={"type": "text", "text": _text(rng, shape.result_chars)},
                    tool_use_id=f"toolu_{index:02d}{turn:02d}",
                    session=session,
                ),
                agent=agent,
            )
            await _record(
                home,
                project,
                stop(f"done with turn {turn + 1}", session=session),
                agent=agent,
            )

    note = "Pick up the release: the version is bumped, the tag is not cut."
    await store.write(
        proj,
        HANDOFF_PATH,
        handoff_document(
            actor=f"claude-code:{sessions[-1]}",
            written=datetime.now(UTC),
            note=note,
            conversation=sessions[-1],
        ),
        actor=f"claude-code:{sessions[-1]}",
    )
    subprocess.run(
        [
            str(console),
            "messages",
            "send",
            "--root",
            str(home),
            "--scope",
            proj,
            "--target",
            "/memories",
            "--body",
            "Check upstream issue #123",
            "--json",
        ],
        capture_output=True,
        check=True,
        env={
            "HOME": str(home.parent),
            "NEOSIAN_HOME": str(home),
            "PATH": os.environ["PATH"],
        },
    )
    return Seeded(home=home, user=user, proj=proj, sessions=tuple(sessions))
