"""`neosian search` — the engine end to end over a FileStore root (§32)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from neosian._foundation.conversation.cli_search import run
from neosian._foundation.llm.base import Message, Role
from neosian._foundation.memory.file import FileStore


class _Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err


async def _run(argv: list[str], env: dict[str, str] | None = None) -> _Result:
    out, err = io.StringIO(), io.StringIO()
    code = await run(argv, env or {}, out=out, err=err)
    return _Result(code, out.getvalue(), err.getvalue())


async def _seed(root: Path) -> None:
    store = FileStore(root)
    for conversation, text in (("cc-1", "the cache TTL is 86400"), ("cc-2", "a ttl")):
        await store.append_turn(
            conversation,
            (
                Message(role=Role.USER, content=text),
                Message(role=Role.ASSISTANT, content="noted"),
            ),
            actor=f"claude-code:{conversation}",
        )


class TestHappyPaths:
    async def test_text_is_one_hit_per_line_newest_first(self, tmp_path: Path) -> None:
        await _seed(tmp_path)
        result = await _run(["ttl", "--root", str(tmp_path)])
        assert result.code == 0, result.err
        lines = result.out.splitlines()
        assert lines[0].startswith("[cc-2 #1] ") and lines[0].endswith("  a ttl")
        assert (
            lines[1].startswith("[cc-1 #1] ")
            and "claude-code:cc-1  the cache" in lines[1]
        )

    async def test_the_terms_are_the_words(self, tmp_path: Path) -> None:
        await _seed(tmp_path)
        result = await _run(["Cache", "86400", "--root", str(tmp_path)])
        assert result.code == 0 and result.out.count("\n") == 1

    async def test_json_is_one_object(self, tmp_path: Path) -> None:
        await _seed(tmp_path)
        result = await _run(["ttl", "--root", str(tmp_path), "--json"])
        assert result.code == 0
        envelope = json.loads(result.out)
        assert envelope["query"] == "ttl" and envelope["conversations"] is None
        assert envelope["limit"] == 20 and envelope["client"] is None
        assert [h["conversation_id"] for h in envelope["hits"]] == ["cc-2", "cc-1"]
        assert envelope["hits"][1]["snippet"] == "the cache TTL is 86400"
        assert envelope["hits"][1]["created_at"].endswith("Z")

    async def test_conversation_narrows_and_repeats(self, tmp_path: Path) -> None:
        await _seed(tmp_path)
        one = await _run(["ttl", "--root", str(tmp_path), "--conversation", "cc-1"])
        assert one.out.count("\n") == 1 and "[cc-1 #1]" in one.out
        both = await _run(
            [
                "ttl",
                "--root",
                str(tmp_path),
                "--conversation",
                "cc-1",
                "--conversation",
                "cc-2",
            ]
        )
        assert both.out.count("\n") == 2

    async def test_the_limit_cuts_to_the_newest(self, tmp_path: Path) -> None:
        await _seed(tmp_path)
        result = await _run(["ttl", "--root", str(tmp_path), "--limit", "1"])
        assert result.out.count("\n") == 1 and "[cc-2 #1]" in result.out

    async def test_no_hit_is_an_answer(self, tmp_path: Path) -> None:
        await _seed(tmp_path)
        result = await _run(["pelican", "--root", str(tmp_path)])
        assert result.code == 0
        assert result.out == "no turn matches every term of 'pelican'\n"


class TestGrammarTier:
    @pytest.mark.parametrize(
        "argv",
        [
            ["   "],
            ["x", "--limit", "0"],
            ["x", "--limit", "501"],
            ["x", "--conversation", "a b"],
            ["x", "--url", "ftp://x"],
        ],
    )
    async def test_exit_two_constructs_nothing(
        self, tmp_path: Path, argv: list[str]
    ) -> None:
        root = tmp_path / "never"
        result = await _run([*argv, "--root", str(root)])
        assert result.code == 2, result.out
        assert not root.exists()

    async def test_no_term_is_usage(self, tmp_path: Path) -> None:
        result = await _run(["--root", str(tmp_path)])
        assert result.code == 2 and "usage" in result.err


class TestTheDaemonBranch:
    async def test_a_refused_connection_is_tier_one(self) -> None:
        result = await _run(
            ["x", "--url", "http://127.0.0.1:1", "--json"],
            {"NEOSIAN_CLIENT_TOKEN": "abc"},
        )
        assert result.code == 1
        assert result.err.startswith("error:")
        assert "error" in json.loads(result.out)
