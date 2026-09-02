"""The `maintain` CLI verb (DESIGN §16, §14.2). Zero keys.

Not a dispatch command: the verb runs the gardener engine, keyless by
default, `--model` through the injected client factory. Its `--json`
envelope is its own (writes/model/usage/cost_micro_usd), never the
six-command `ToolResult` shape. Exit tiers: 0 · 1 (a requested model
stage degraded — deterministic actions still landed) · 2 (grammar,
unknown model, missing factory or key — nothing constructed).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from neosian._foundation.llm.base import BaseLLMClient, Usage
from neosian._foundation.llm.fake import FakeClient, FakeScript, FakeTurn
from neosian._foundation.memory.cli import run
from neosian._foundation.memory.maintenance import (
    MaintainDeleteOp,
    MaintenanceBatch,
)

_ENV: dict[str, str] = {}
_USAGE = Usage(input_tokens=40, output_tokens=4)


class _Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err


async def _run(
    argv: list[str],
    root: Path,
    *,
    client_factory: Callable[..., BaseLLMClient] | None = None,
) -> _Result:
    out, err = io.StringIO(), io.StringIO()
    code = await run(
        [*argv, "--root", str(root), "--scope", "user:me"],
        _ENV,
        stdin=io.StringIO(""),
        out=out,
        err=err,
        client_factory=client_factory,
    )
    return _Result(code, out.getvalue(), err.getvalue())


async def _seed_duplicates(root: Path) -> None:
    for name in ("dup-a", "dup-b"):
        created = await _run(
            ["create", f"/memories/{name}", "--content", "Prefers dark mode"], root
        )
        assert created.code == 0


class TestGrammarTier:
    async def test_an_unknown_model_is_a_grammar_error(self, tmp_path: Path) -> None:
        result = await _run(["maintain", "--model", "bogus"], tmp_path)
        assert result.code == 2
        assert "unknown model 'bogus'" in result.err
        assert "fake" in result.err  # the registry is named — the fix in the error

    async def test_a_negative_min_age_is_a_grammar_error(self, tmp_path: Path) -> None:
        result = await _run(["maintain", "--min-age-days", "-1"], tmp_path)
        assert result.code == 2
        assert "--min-age-days must not be negative" in result.err

    async def test_model_without_a_factory_constructs_nothing(
        self, tmp_path: Path
    ) -> None:
        await _seed_duplicates(tmp_path)
        result = await _run(["maintain", "--model", "fake"], tmp_path)
        assert result.code == 2
        assert "not available from this entry point" in result.err
        listing = await _run(["view"], tmp_path)
        assert "dup-a" in listing.out and "dup-b" in listing.out


class TestKeylessMode:
    async def test_the_deterministic_pass_over_a_real_root(
        self, tmp_path: Path
    ) -> None:
        await _seed_duplicates(tmp_path)
        result = await _run(["maintain", "--min-age-days", "0"], tmp_path)
        assert result.code == 0
        assert "delete /memories/dup-b" in result.out
        assert "model pass" not in result.out
        listing = await _run(["view"], tmp_path)
        assert "dup-a" in listing.out and "dup-b" not in listing.out

    async def test_a_clean_store_says_nothing_to_do(self, tmp_path: Path) -> None:
        result = await _run(["maintain"], tmp_path)
        assert result.code == 0
        assert result.out == "nothing to do\n"

    async def test_the_json_envelope(self, tmp_path: Path) -> None:
        await _seed_duplicates(tmp_path)
        result = await _run(["maintain", "--min-age-days", "0", "--json"], tmp_path)
        assert result.code == 0
        envelope = json.loads(result.out)
        assert envelope["writes"] == [
            {"command": "delete", "path": "/memories/dup-b", "version": None}
        ]
        assert envelope["model"] is None
        assert envelope["usage"] is None
        assert envelope["cost_micro_usd"] is None
        assert envelope["degraded"] is None


class TestModelMode:
    async def test_the_scripted_model_stage_lands_and_reports_spend(
        self, tmp_path: Path
    ) -> None:
        created = await _run(
            ["create", "/memories/stale", "--content", "old claim"], tmp_path
        )
        assert created.code == 0
        batch = MaintenanceBatch(
            ops=[MaintainDeleteOp(command="delete", path="/memories/stale")]
        ).model_dump_json()
        fake = FakeClient(FakeScript(turns=(FakeTurn(content=batch, usage=_USAGE),)))
        result = await _run(
            ["maintain", "--model", "fake", "--min-age-days", "0", "--json"],
            tmp_path,
            client_factory=lambda _: fake,
        )
        assert result.code == 0
        envelope = json.loads(result.out)
        assert envelope["writes"] == [
            {"command": "delete", "path": "/memories/stale", "version": None}
        ]
        assert envelope["model"] is not None
        assert envelope["usage"]["input_tokens"] == 40

    async def test_a_degraded_model_stage_exits_one_and_says_so(
        self, tmp_path: Path
    ) -> None:
        await _seed_duplicates(tmp_path)
        fake = FakeClient(FakeScript(turns=(FakeTurn(content="not json"),)))
        result = await _run(
            ["maintain", "--model", "fake", "--min-age-days", "0"],
            tmp_path,
            client_factory=lambda _: fake,
        )
        assert result.code == 1
        assert "the model stage failed (Maintenance failed: " in result.err
        assert "deterministic actions still landed" in result.err
        assert "delete /memories/dup-b" in result.out  # stage 1 still ran


class TestRealBinary:
    def test_keyless_maintain_over_the_process_boundary(self, tmp_path: Path) -> None:
        """One interpreter start: the verb exists on the installed console
        script and gardens a real root keylessly (the ledger #78 tiering —
        the fork layer pinned once, not per scenario)."""
        name = "neosian.exe" if sys.platform == "win32" else "neosian"
        binary = Path(sys.executable).parent / name
        assert binary.is_file(), f"console script missing: {binary} (run `uv sync`)"
        env = {k: v for k, v in os.environ.items() if k != "NEOSIAN_POSTGRES_DSN"}
        flags = ["--root", str(tmp_path / "r"), "--scope", "user:me"]

        def invoke(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(binary), "memory", *args, *flags],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=60,
                check=False,
            )

        for doc in ("a", "b"):
            created = invoke("create", f"/memories/{doc}", "--content", "same fact")
            assert created.returncode == 0, created.stderr
        gardened = invoke("maintain", "--min-age-days", "0", "--json")
        assert gardened.returncode == 0, gardened.stderr
        envelope = json.loads(gardened.stdout)
        assert envelope["writes"] == [
            {"command": "delete", "path": "/memories/b", "version": None}
        ]
