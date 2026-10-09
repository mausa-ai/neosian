"""`neosian redact` / `neosian prune`: the engine end to end over a
FileStore root (N8, DESIGN §38)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from neosian._foundation.conversation.cli_erasure import run
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.file import FileStore
from neosian._foundation.memory.sessions import sessions_document, sessions_path
from tests.support.clock import ManualClock

_SCOPES = ("user:me", "user:me/proj:p")


class _Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err


async def _run(argv: list[str], env: dict[str, str] | None = None) -> _Result:
    out, err = io.StringIO(), io.StringIO()
    code = await run(argv, env or {}, out=out, err=err)
    return _Result(code, out.getvalue(), err.getvalue())


def _exchange(text: str) -> tuple[Message, ...]:
    return (
        Message(role=Role.USER, content=text),
        Message(role=Role.ASSISTANT, content="noted"),
    )


async def _seed(root: Path, *, clock: object = None) -> FileStore:
    """One conversation of three turns, its sessions document in two scopes."""
    store = FileStore(root, clock=clock or ManualClock())  # type: ignore[arg-type]
    for n in range(1, 4):
        await store.append_turn("ses_1", _exchange(f"turn {n} hunter{n}"), actor="w")
    for scope in _SCOPES:
        await store.write(
            scope,
            sessions_path("ses_1"),
            sessions_document(
                agent="claude-code",
                session_id="ses_1",
                started=datetime(2026, 8, 19, tzinfo=UTC),
                last_prompt="turn 3 hunter3",
                turns=3,
            ),
            actor="claude-code:ses_1#3",
        )
    return store


class TestRedact:
    async def test_named_turns_blank_those_and_leave_the_documents(
        self, tmp_path: Path
    ) -> None:
        store = await _seed(tmp_path)
        result = await _run(
            ["redact", "ses_1", "--turns", "1,3", "--root", str(tmp_path)]
        )
        assert (result.code, result.out) == (0, "redacted 2 turns of ses_1\n")
        turns = await store.read_turns("ses_1")
        assert [t.redacted for t in turns] == [True, False, True]
        assert "hunter2" in (turns[1].messages[0].content or "")
        for scope in _SCOPES:
            document = await store.read(scope, sessions_path("ses_1"))
            assert document is not None and not document.redacted

    async def test_all_blanks_the_conversation_and_its_sessions_documents(
        self, tmp_path: Path
    ) -> None:
        store = await _seed(tmp_path)
        result = await _run(
            ["redact", "ses_1", "--all", "--actor", "cli:ops", "--root", str(tmp_path)]
        )
        assert result.code == 0, result.err
        assert result.out.splitlines() == [
            "redacted 3 turns of ses_1",
            "redacted /sessions/ses_1 in user:me",
            "redacted /sessions/ses_1 in user:me/proj:p",
        ]
        assert all(t.redacted for t in await store.read_turns("ses_1"))
        for scope in _SCOPES:
            document = await store.read(scope, sessions_path("ses_1"))
            assert document is not None and document.redacted
            assert "hunter3" not in document.content
        (act,) = await store.turn_redactions(conversations=["ses_1"])
        assert (act.turns, act.actor) == ((1, 2, 3), "cli:ops")
        assert (
            "hunter"
            not in (tmp_path / "conversations" / "ses_1" / "turns.jsonl").read_text()
        )

    async def test_json_is_one_object(self, tmp_path: Path) -> None:
        await _seed(tmp_path)
        result = await _run(
            ["redact", "ses_1", "--through", "2", "--root", str(tmp_path), "--json"]
        )
        assert result.code == 0
        assert json.loads(result.out) == {
            "verb": "redact",
            "conversation_id": "ses_1",
            "count": 2,
            "documents": [],
            "client": None,
        }

    async def test_an_unknown_conversation_matches_nothing(
        self, tmp_path: Path
    ) -> None:
        await _seed(tmp_path)
        result = await _run(["redact", "never", "--all", "--root", str(tmp_path)])
        assert (result.code, result.out) == (0, "no turns matched in never\n")


class _Instants:
    def __init__(self, *instants: datetime) -> None:
        self._instants = list(instants)

    def now(self) -> datetime:
        return self._instants.pop(0) if len(self._instants) > 1 else self._instants[0]


class TestPrune:
    @pytest.fixture
    async def home(self, tmp_path: Path) -> FileStore:
        """`old` ended in August 2026, `fresh` a moment ago; both listed."""
        now = datetime.now(UTC)
        clock = _Instants(
            datetime(2026, 8, 19, 10, 0, tzinfo=UTC),
            datetime(2026, 8, 19, 10, 1, tzinfo=UTC),
            now - timedelta(minutes=5),
            now,
        )
        store = FileStore(tmp_path, clock=clock)
        for n in range(2):
            await store.append_turn("old", _exchange(f"old secret {n}"))
        await store.append_turn("fresh", _exchange("fresh words"))
        for name in ("old", "fresh"):
            await store.write(
                _SCOPES[0],
                sessions_path(name),
                sessions_document(
                    agent="codex",
                    session_id=name,
                    started=datetime(2026, 8, 19, tzinfo=UTC),
                    last_prompt=f"{name} prompt",
                    turns=1,
                ),
                actor=f"codex:{name}#1",
            )
        return store

    async def test_a_dry_run_plans_and_writes_nothing(
        self, home: FileStore, tmp_path: Path
    ) -> None:
        result = await _run(
            [
                "prune",
                "--older-than",
                "30d",
                "--dry-run",
                "--root",
                str(tmp_path),
                "--json",
            ]
        )
        assert result.code == 0, result.err
        envelope = json.loads(result.out)
        assert envelope["verb"] == "prune" and envelope["dry_run"] is True
        assert envelope["cutoff"].endswith("Z")
        assert [c["conversation_id"] for c in envelope["conversations"]] == ["old"]
        assert envelope["conversations"][0]["turns"] == 2
        assert envelope["conversations"][0]["last_at"] == "2026-08-19T10:01:00Z"
        assert envelope["turns"] == 2
        assert envelope["documents"] == [{"scope": "user:me", "path": "sessions/old"}]
        assert not any(t.redacted for t in await home.read_turns("old"))
        document = await home.read(_SCOPES[0], sessions_path("old"))
        assert document is not None and not document.redacted
        assert await home.turn_redactions() == ()

    async def test_the_real_run_leaves_skeletons_where_the_old_sessions_were(
        self, home: FileStore, tmp_path: Path
    ) -> None:
        result = await _run(["prune", "--older-than", "30d", "--root", str(tmp_path)])
        assert result.code == 0, result.err
        assert result.out.splitlines() == [
            "old  2 turns  last 2026-08-19T10:01:00Z",
            "redacted 1 conversation, 2 turns older than "
            + result.out.splitlines()[1].rsplit(" ", 1)[1],
            "redacted /sessions/old in user:me",
        ]
        old = await home.read_turns("old")
        assert [(t.turn, t.messages, t.redacted) for t in old] == [
            (1, (), True),
            (2, (), True),
        ]
        (fresh,) = await home.read_turns("fresh")
        assert not fresh.redacted and "fresh words" in (fresh.messages[0].content or "")
        document = await home.read(_SCOPES[0], sessions_path("old"))
        assert document is not None and document.redacted
        kept = await home.read(_SCOPES[0], sessions_path("fresh"))
        assert kept is not None and not kept.redacted
        (act,) = await home.turn_redactions()
        assert (act.conversation_id, act.turns, act.actor) == (
            "old",
            (1, 2),
            "cli:local",
        )
        # A second pass finds the blanked conversation and leaves it alone.
        again = await _run(["prune", "--older-than", "30d", "--root", str(tmp_path)])
        assert again.out.startswith("nothing older than ")
        assert len(await home.turn_redactions()) == 1

    async def test_hours_are_a_duration_too(
        self, home: FileStore, tmp_path: Path
    ) -> None:
        del home
        result = await _run(
            [
                "prune",
                "--older-than",
                "1h",
                "--dry-run",
                "--root",
                str(tmp_path),
                "--json",
            ]
        )
        assert [
            c["conversation_id"] for c in json.loads(result.out)["conversations"]
        ] == ["old"]


class TestGrammarTier:
    @pytest.mark.parametrize(
        "argv",
        [
            ["redact", "ses_1"],  # no selector
            ["redact", "ses_1", "--all", "--through", "2"],  # two selectors
            ["redact", "ses_1", "--through", "0"],
            ["redact", "ses_1", "--turns", "0"],
            ["redact", "ses_1", "--turns", "x"],
            ["redact", "a/b", "--all"],  # an invalid id
            ["redact", "ses_1", "--all", "--actor", "nope"],
            ["prune"],  # no cutoff
            ["prune", "--older-than", "5m"],
            ["prune", "--older-than", "0d"],
            ["prune", "--older-than", "30d", "--url", "ftp://x"],
            ["erase", "ses_1"],  # an unknown verb
        ],
    )
    async def test_bad_invocations_exit_two_with_nothing_built(
        self, tmp_path: Path, argv: list[str]
    ) -> None:
        root = tmp_path / "never"
        full = argv if "--url" in argv else [*argv, "--root", str(root)]
        result = await _run(full, {"NEOSIAN_CLIENT_TOKEN": "t"})
        assert result.code == 2, result.err
        assert not root.exists()

    async def test_help_answers_at_the_grammar(self, tmp_path: Path) -> None:
        for verb in ("redact", "prune"):
            result = await _run([verb, "--help"])
            assert result.code == 0
            assert "--json" in result.out and "irreversible" in result.out.lower()
        assert not (tmp_path / "never").exists()


class TestTierOne:
    async def test_a_backend_without_the_eraser_is_refused_by_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Plain(FileStore):
            client = "claude-code:laptop"
            redact_turns = None  # type: ignore[assignment]

            async def aclose(self) -> None:
                pass

        async def connect(url: str, *, token: str) -> Plain:
            del url, token
            return Plain(tmp_path / "plain")

        import neosian._foundation.server.remote as remote_module

        monkeypatch.setattr(remote_module.RemoteStore, "connect", connect)
        result = await _run(
            ["redact", "c1", "--all", "--url", "http://127.0.0.1:6367", "--json"],
            {"NEOSIAN_CLIENT_TOKEN": "abc"},
        )
        assert result.code == 1
        assert "does not implement Erasable" in result.err
        assert "error" in json.loads(result.out)

    async def test_the_daemon_branch_reaches_connect_and_reports_the_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backing = await _seed(tmp_path / "backing")
        seen: dict[str, str] = {}

        class Remote(FileStore):
            client = "claude-code:laptop"

            async def aclose(self) -> None:
                seen["closed"] = "yes"

        async def connect(url: str, *, token: str) -> Remote:
            seen.update(url=url, token=token)
            return Remote(tmp_path / "backing")

        import neosian._foundation.server.remote as remote_module

        monkeypatch.setattr(remote_module.RemoteStore, "connect", connect)
        result = await _run(
            [
                "redact",
                "ses_1",
                "--through",
                "1",
                "--url",
                "http://127.0.0.1:6367",
                "--json",
            ],
            {"NEOSIAN_CLIENT_TOKEN": "abc"},
        )
        assert result.code == 0, result.err
        assert seen == {"url": "http://127.0.0.1:6367", "token": "abc", "closed": "yes"}
        assert json.loads(result.out)["client"] == "claude-code:laptop"
        assert (await backing.read_turns("ses_1"))[0].redacted is True

    async def test_a_refused_connection_is_tier_one(self) -> None:
        result = await _run(
            ["prune", "--older-than", "1d", "--url", "http://127.0.0.1:1", "--json"],
            {"NEOSIAN_CLIENT_TOKEN": "abc"},
        )
        assert result.code == 1
        assert result.err.startswith("error:")
