"""`neosian setup` (DESIGN §30.3): the clients found, both installers each,
print first, `--write` lands both files, `status` green afterwards."""

import io
import json
import sys
from collections.abc import Mapping
from pathlib import Path

from neosian._cli.setup import present_clients, run_setup
from neosian._cli.status import collect
from neosian._foundation.shared.client_config import Environment


def _context(tmp_path: Path) -> Environment:
    project = tmp_path / "demo proj"
    project.mkdir(exist_ok=True)
    return Environment(
        home=tmp_path,
        cwd=project,
        platform="darwin",
        env={},
        executable=sys.executable,  # a real file: the interpreter resolves
    )


def _env(tmp_path: Path) -> Mapping[str, str]:
    return {"NEOSIAN_HOME": str(tmp_path / "home")}


def _run(tmp_path: Path, argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_setup(argv, _env(tmp_path), context=_context(tmp_path), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class TestDetection:
    def test_no_client_is_an_environment_error(self, tmp_path: Path) -> None:
        assert present_clients(_context(tmp_path)) == []
        code, out, err = _run(tmp_path, ["--json"])
        assert code == 1 and "no client found" in json.loads(out)["error"]
        assert "error:" in err

    def test_the_evidence_directories_name_the_clients(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".config" / "opencode").mkdir(parents=True)
        assert present_clients(_context(tmp_path)) == ["claude-code", "opencode"]


class TestPrintThenWrite:
    def test_print_mode_writes_nothing_and_names_the_paths(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".claude").mkdir()
        context = _context(tmp_path)
        code, out, err = _run(tmp_path, [])
        assert code == 0, err
        assert out.splitlines()[0] == "Claude Code"
        assert f"  mcp   {context.cwd / '.mcp.json'}  would write" in out
        assert "hooks " in out and "would write" in out
        assert "re-run with --write" in err and "one writer" in err
        assert not (context.cwd / ".mcp.json").exists()
        assert not (context.cwd / ".claude").exists()

    def test_write_lands_both_files_and_status_is_green(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        context = _context(tmp_path)
        code, out, err = _run(tmp_path, ["--write", "--json"])
        assert code == 0, err
        payload = json.loads(out)
        assert payload["written"] is True
        (row,) = payload["clients"]
        assert row["mcp"]["written"] and row["hooks"]["written"]
        registered = json.loads((context.cwd / ".mcp.json").read_text())
        assert "neosian-memory" in registered["mcpServers"]
        hooks = json.loads((context.cwd / ".claude" / "settings.json").read_text())
        assert "Stop" in hooks["hooks"]
        assert err.count("hint: one writer") == 1

        import asyncio

        status = asyncio.run(collect(context, _env(tmp_path)))
        claude = status.clients[0]
        assert claude.installed and claude.mcp_registered and claude.hooks_present
        assert claude.interpreter_resolves is True
        assert claude.root == str(tmp_path / "home")
        assert len(status.one_writer) == 1  # hooks + MCP on one root, named

    def test_codex_mcp_stays_print_only_with_its_apply_line(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".codex").mkdir()
        code, out, err = _run(tmp_path, ["--client", "codex", "--write"])
        assert code == 1, err  # the refusal is reported, the hooks still land
        assert "print-only" not in out and "refused" in out
        assert (_context(tmp_path).cwd / ".codex" / "hooks.json").exists()

    def test_client_narrows_to_one(self, tmp_path: Path) -> None:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".config" / "opencode").mkdir(parents=True)
        code, out, _ = _run(tmp_path, ["--client", "opencode", "--json"])
        assert code == 0
        assert [r["client"] for r in json.loads(out)["clients"]] == ["opencode"]
