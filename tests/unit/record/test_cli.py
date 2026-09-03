"""`neosian record` — the engine end to end over a FileStore root and
through the in-process daemon (§20.9)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from neosian._foundation.llm.base import Role
from neosian._foundation.memory.file import FileStore
from neosian._foundation.record.cli import run
from neosian._foundation.server.app import build_app
from tests.unit.record.payloads import SESSION, prompt, stop, tool


class _Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err


async def _run(
    argv: list[str], payload: object, env: dict[str, str] | None = None
) -> _Result:
    out, err = io.StringIO(), io.StringIO()
    text = payload if isinstance(payload, str) else json.dumps(payload)
    code = await run(argv, env or {}, stdin=io.StringIO(text), out=out, err=err)
    return _Result(code, out.getvalue(), err.getvalue())


def _flags(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--root",
        str(tmp_path / "mem"),
        "--scope",
        "user:me",
        "--spool",
        str(tmp_path / "spool"),
        *extra,
    ]


async def _span(argv: list[str], *, prompt_text: str = "q") -> list[_Result]:
    return [
        await _run(argv, prompt(prompt_text)),
        await _run(argv, tool()),
        await _run(argv, stop("a")),
    ]


class TestTheSpan:
    async def test_three_payloads_land_one_turn_and_a_document(
        self, tmp_path: Path
    ) -> None:
        results = await _span(_flags(tmp_path))
        assert [r.code for r in results] == [0, 0, 0], [r.err for r in results]
        assert all(r.out == "" and r.err == "" for r in results)  # silent: injected
        store = FileStore(tmp_path / "mem")
        (turn,) = await store.read_turns(SESSION)
        assert turn.actor == f"claude-code:{SESSION}"
        assert [m.role for m in turn.messages] == [
            Role.USER,
            Role.ASSISTANT,
            Role.TOOL,
            Role.ASSISTANT,
        ]
        document = await store.read("user:me", f"sessions/{SESSION}")
        assert document is not None and "- turns: 1\n" in document.content
        (row,) = await store.versions("user:me", f"sessions/{SESSION}")
        assert row.actor == f"claude-code:{SESSION}#1"
        assert not (tmp_path / "spool" / f"{SESSION}.jsonl").exists()

    async def test_a_second_span_is_turn_two_and_the_document_grows(
        self, tmp_path: Path
    ) -> None:
        await _span(_flags(tmp_path), prompt_text="first")
        await _span(_flags(tmp_path), prompt_text="second")
        store = FileStore(tmp_path / "mem")
        turns = await store.read_turns(SESSION)
        assert [t.turn for t in turns] == [1, 2]
        document = await store.read("user:me", f"sessions/{SESSION}")
        assert document is not None and document.version == 2
        assert "- turns: 2\n" in document.content
        assert "- last prompt: second\n" in document.content
        started = turns[0].created_at.isoformat().replace("+00:00", "Z")
        assert f"- started: {started}\n" in document.content

    async def test_the_json_envelopes(self, tmp_path: Path) -> None:
        argv = _flags(tmp_path, "--json")
        spooled = json.loads((await _run(argv, prompt())).out)
        assert spooled["disposition"] == "spooled" and spooled["turn"] is None
        skipped = json.loads((await _run(argv, tool(agent_id="sub"))).out)
        assert skipped["disposition"] == "skipped"
        recorded = json.loads((await _run(argv, stop())).out)
        assert recorded == {
            "event": "Stop",
            "session_id": SESSION,
            "actor": f"claude-code:{SESSION}",
            "disposition": "recorded",
            "conversation_id": SESSION,
            "turn": 1,
            "document": f"/memories/sessions/{SESSION}",
            "client": None,
        }

    async def test_an_untracked_event_touches_nothing(self, tmp_path: Path) -> None:
        payload = {**stop(), "hook_event_name": "SessionStart"}
        result = await _run(_flags(tmp_path, "--json"), payload)
        assert result.code == 0
        assert json.loads(result.out)["disposition"] == "ignored"
        assert not (tmp_path / "spool").exists() and not (tmp_path / "mem").exists()

    async def test_an_empty_span_lands_nothing(self, tmp_path: Path) -> None:
        result = await _run(_flags(tmp_path, "--json"), stop(""))
        assert result.code == 0
        assert json.loads(result.out)["disposition"] == "empty"
        assert not (tmp_path / "mem" / "conversations").exists()
        assert not (tmp_path / "spool" / f"{SESSION}.jsonl").exists()

    async def test_the_agent_kind_is_the_actor_and_codex_gets_a_decision(
        self, tmp_path: Path
    ) -> None:
        """Codex's Stop hook expects JSON on stdout (its reference); the
        verb answers an empty decision there and nothing anywhere else."""
        argv = _flags(tmp_path, "--agent", "codex")
        opened = await _run(argv, prompt())
        assert opened.out == ""
        closed = await _run(argv, stop())
        assert closed.out == "{}\n" and closed.err == ""
        (turn,) = await FileStore(tmp_path / "mem").read_turns(SESSION)
        assert turn.actor == f"codex:{SESSION}"


class TestTierOne:
    @pytest.mark.parametrize(
        ("payload", "reason"),
        [
            ("{not json", "not JSON"),
            ({"hook_event_name": "Stop"}, "session_id"),
            ({**stop(), "session_id": "a/b"}, "conversation_id_invalid"),
        ],
    )
    async def test_bad_stdin_exits_1_with_nothing_built(
        self, tmp_path: Path, payload: object, reason: str
    ) -> None:
        result = await _run(_flags(tmp_path, "--json"), payload)
        assert result.code == 1
        assert result.err.startswith("error:") and "hint:" in result.err
        assert reason in result.err and reason in json.loads(result.out)["error"]
        assert not (tmp_path / "mem").exists()

    async def test_a_failed_store_keeps_the_spool(self, tmp_path: Path) -> None:
        (tmp_path / "mem").write_text("a file where the root should be")
        argv = _flags(tmp_path)
        results = await _span(argv)
        assert [r.code for r in results] == [0, 0, 1]
        assert "error:" in results[2].err and "the spool keeps it" in results[2].err
        lines = (tmp_path / "spool" / f"{SESSION}.jsonl").read_text().splitlines()
        assert [json.loads(line)["kind"] for line in lines] == [
            "prompt",
            "tool",
            "stop",
        ]


class TestGrammarTier:
    @pytest.mark.parametrize(
        "argv",
        [
            ["--root", "never"],  # no scope
            ["--root", "never", "--mount", "scope=user:me,path=memories,ro"],
            ["--root", "never", "--scope", "user:me", "--agent", "Claude Code"],
            ["--root", "never", "--scope", "nobody"],
        ],
    )
    async def test_bad_invocations_exit_two(
        self, tmp_path: Path, argv: list[str]
    ) -> None:
        argv = [str(tmp_path / a) if a == "never" else a for a in argv]
        result = await _run(argv, stop())
        assert result.code == 2, result.err
        assert result.out == ""
        assert not (tmp_path / "never").exists()

    async def test_help_names_the_flags(self) -> None:
        result = await _run(["--help"], "")
        assert result.code == 0
        assert "--spool" in result.out and "--agent" in result.out


class TestTheDaemon:
    async def test_the_prefix_lands_on_the_turn_and_the_document(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Through `--url` the daemon asserts the client: both writes carry
        `<client>/claude-code:<session>` (§20.4)."""
        import neosian._foundation.server.remote as remote_module

        backing = FileStore(tmp_path / "backing")
        app = await build_app(backing, token="t")
        connect = remote_module.RemoteStore.connect

        async def patched(
            url: str, *, token: str, **_: Any
        ) -> remote_module.RemoteStore:
            return await connect(
                url, token=token, transport=httpx.ASGITransport(app=app)
            )

        monkeypatch.setattr(remote_module.RemoteStore, "connect", patched)
        argv = [
            "--url",
            "http://state-process",
            "--scope",
            "user:me",
            "--spool",
            str(tmp_path / "spool"),
            "--json",
        ]
        env = {"NEOSIAN_CLIENT_TOKEN": "t"}
        await _run(argv, prompt(), env)
        result = await _run(argv, stop(), env)
        assert result.code == 0, result.err
        assert json.loads(result.out)["client"] == "client:default"
        (turn,) = await backing.read_turns(SESSION)
        assert turn.actor == f"client:default/claude-code:{SESSION}"
        (row,) = await backing.versions("user:me", f"sessions/{SESSION}")
        assert row.actor == f"client:default/claude-code:{SESSION}#1"

    async def test_a_refused_connection_is_tier_one(self, tmp_path: Path) -> None:
        argv = [
            "--url",
            "http://127.0.0.1:1",
            "--scope",
            "user:me",
            "--spool",
            str(tmp_path),
        ]
        await _run(argv, prompt(), {"NEOSIAN_CLIENT_TOKEN": "t"})
        result = await _run(argv, stop(), {"NEOSIAN_CLIENT_TOKEN": "t"})
        assert result.code == 1
        assert result.err.startswith("error:")
