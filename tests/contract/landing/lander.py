"""Helpers for the remote landing contracts (the T1e1c slice of the T1e1
plan): turns queued for a URL by the real Stop hook while the test holds the
conversation's landing lock, `neosian record-land --home H` run in the
foreground, and the served store read back. No product code is imported."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from tests.contract.landing.conftest import Hold
from tests.contract.support import ADMIN, Binary, Server

PART_BYTES = 983_040


def draft_id(conversation: str, line: str) -> str:
    """`p` and 32 hex digits of SHA-256 over the id, a line feed, the line."""
    return "p" + hashlib.sha256(f"{conversation}\n{line}".encode()).hexdigest()[:32]


def fresh_id(conversation: str, line: str) -> str:
    """The id of the one retry 17.4 allows: the same digest, then `\\n1`."""
    material = f"{conversation}\n{line}\n1"
    return "p" + hashlib.sha256(material.encode()).hexdigest()[:32]


def enable(binary: Binary, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    enabled = binary.run("records", "enable", "--root", str(root))
    assert enabled.returncode == 0, enabled.stderr


def served_root(binary: Binary) -> Path:
    """The root `serve_on` serves."""
    return binary.directory / "served"


def environment(binary: Binary) -> dict[str, str]:
    return {**binary.environment(), "NEOSIAN_CLIENT_TOKEN": ADMIN}


def hook(
    binary: Binary,
    url: str,
    session: str,
    name: str,
    spool: Path | None = None,
    **payload: Any,
) -> subprocess.CompletedProcess[str]:
    tree = ["--spool", str(spool)] if spool else []
    return subprocess.run(
        [str(binary.executable), "record", "--url", url, *tree]
        + ["--scope", "user:contract", "--json"],
        input=json.dumps({"session_id": session, "hook_event_name": name, **payload}),
        env=environment(binary),
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def queue(
    binary: Binary,
    hold: Hold,
    url: str,
    session: str,
    prompt: str,
    result: int = 0,
    tree: Path | None = None,
) -> int:
    """A span of `prompt` and, with `result`, one tool round whose result
    holds that many characters, closed by the Stop hook while the test
    holds the conversation's lock, so the turn waits. Returns its `n`."""
    spool = tree or binary.home / "spool"
    lines: list[dict[str, Any]] = [{"kind": "prompt", "text": prompt}]
    if result:
        text = ('make: built é 中 "ok"\t\n' * (result // 20 + 1))[:result]
        lines.append(
            {
                "kind": "tool",
                "id": "call-1",
                "name": "Bash",
                "input": {"command": "make"},
                "response": text,
            }
        )
    spool.mkdir(parents=True, exist_ok=True)
    span = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    (spool / f"{session}.jsonl").write_text(span, encoding="utf-8")
    pending = spool / "pending" / session
    with hold(pending):
        stopped = hook(
            binary,
            url,
            session,
            "Stop",
            spool=tree,
            last_assistant_message=f"{prompt} done",
        )
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert json.loads(stopped.stdout)["disposition"] == "pending"
    return max(int(path.stem) for path in pending.glob("*.jsonl"))


def opening(binary: Binary, session: str, n: int) -> str:
    path = binary.home / "spool" / "pending" / session / f"{n}.jsonl"
    return path.read_text(encoding="utf-8").split("\n", 1)[0]


def state(binary: Binary, session: str, n: int) -> Any:
    path = binary.home / "spool" / "pending" / session / f"{n}.state"
    return json.loads(path.read_text())


def land(binary: Binary) -> Any:
    """`neosian record-land --home H` in the foreground: its summary."""
    landed = lander(binary)
    assert landed.returncode == 0, landed.stdout + landed.stderr
    return json.loads(landed.stdout)


def lander(
    binary: Binary, tree: Path | None = None
) -> subprocess.CompletedProcess[str]:
    spool = ["--spool", str(tree)] if tree else []
    return subprocess.run(
        [str(binary.executable), "record-land", "--home", str(binary.home), *spool],
        env=environment(binary),
        cwd=binary.directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def popen(binary: Binary) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [str(binary.executable), "record-land", "--home", str(binary.home)],
        env=environment(binary),
        cwd=binary.directory,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def turns(server: Server, session: str) -> list[Any]:
    return list(
        server.post("conversation/read_turns", conversation_id=session)["turns"]
    )


def prompts(server: Server, session: str) -> list[str]:
    return [turn["messages"][0]["content"] for turn in turns(server, session)]


def rows(binary: Binary, session: str) -> list[Any]:
    log = served_root(binary) / "conversations" / session / "turns.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()]


def pending(binary: Binary, session: str) -> list[int]:
    directory = binary.home / "spool" / "pending" / session
    return sorted(int(path.stem) for path in directory.glob("*.jsonl"))


def held(binary: Binary, session: str, n: int) -> Any:
    path = binary.home / "spool" / "held" / session / f"{n}.reason"
    return json.loads(path.read_text())


def legacy_body(conversation: str, messages: list[Any], actor: str) -> bytes:
    """`append_turn`'s body as every release since 2.16.0 sends it: the
    value indented by two spaces, non-ASCII raw, keys in this order."""
    value = {"conversation_id": conversation, "messages": messages, "actor": actor}
    return json.dumps(value, indent=2, ensure_ascii=False).encode()
