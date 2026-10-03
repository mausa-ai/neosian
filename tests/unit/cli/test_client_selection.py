"""Setup choices and paths are explicit, safe, and invocation-only."""

import io
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from rich.console import Console

from neosian._cli.setup import CLIENTS, run_setup
from neosian._cli.status import run as run_status
from neosian._cli.ui import checklist
from neosian._foundation.shared.client_config import Environment


def context(tmp_path: Path) -> Environment:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return Environment(tmp_path, project, "darwin", {}, sys.executable)


def setup(
    ctx: Environment, *argv: str, answer: str = "", tty: bool = False
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_setup(
        argv,
        {"NEOSIAN_HOME": str(ctx.home / "store")},
        context=ctx,
        out=out,
        err=err,
        stdin=io.StringIO(answer),
        tty=tty,
    )
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("json_output", [False, True])
def test_unattended_write_requires_explicit_selection(
    tmp_path: Path, json_output: bool
) -> None:
    ctx = context(tmp_path)
    (tmp_path / ".pi" / "agent").mkdir(parents=True)
    code, _, err = setup(ctx, "--write", *(["--json"] if json_output else []))
    assert code == 2 and "--yes or --client" in err
    assert list((tmp_path / ".pi" / "agent").iterdir()) == []


def test_json_never_prompts_even_on_terminal(tmp_path: Path) -> None:
    code, out, _ = setup(context(tmp_path), "--write", "--json", tty=True)
    assert code == 2 and "Connect clients" not in out


def test_terminal_unticks_and_reasks_next_time(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".pi" / "agent").mkdir(parents=True)
    code, out, err = setup(ctx, "--write", answer="1\n\n", tty=True)
    assert code == 0, err
    assert "[x] Cursor" in out and "[ ] Cursor" in out
    assert not (tmp_path / ".cursor" / "mcp.json").exists()
    assert (tmp_path / ".pi" / "agent" / "mcp.json").exists()
    assert setup(ctx, "--write", answer="\n", tty=True)[0] == 0
    assert (tmp_path / ".cursor" / "mcp.json").exists()


@pytest.mark.parametrize(("answer", "code"), [("", 1), ("q\n", 1), ("1\n\n", 0)])
def test_cancel_or_empty_selection_writes_nothing(
    tmp_path: Path, answer: str, code: int
) -> None:
    ctx = context(tmp_path)
    base = tmp_path / ".cursor"
    base.mkdir()
    assert setup(ctx, "--write", answer=answer, tty=True)[0] == code
    assert list(base.iterdir()) == []


def test_checklist_validates_without_changing_selection() -> None:
    out = io.StringIO()
    result = checklist(
        Console(file=out), ["one", "two"], io.StringIO("bad\n0\n3\n2\n\n")
    )
    assert result == [0]
    assert out.getvalue().count("Enter numbers from the list") == 3


def test_yes_bypasses_selection_and_duplicate_clients_apply_once(
    tmp_path: Path,
) -> None:
    ctx = context(tmp_path)
    (tmp_path / ".cursor").mkdir()
    code, out, _ = setup(ctx, "--write", "--yes", "--json", tty=True)
    assert code == 0 and len(json.loads(out)["clients"]) == 1
    code, out, _ = setup(
        ctx, "--write", "--client", "cursor", "--client", "cursor", "--json"
    )
    assert code == 0 and len(json.loads(out)["clients"]) == 1


@pytest.mark.parametrize("client", CLIENTS)
def test_locations_reach_both_installers_without_persisting(
    tmp_path: Path, client: str
) -> None:
    ctx = context(tmp_path)
    base = ctx.cwd / "other client"
    base.mkdir()
    # Preview is enough to inspect CLI-owned config paths too.
    code, out, err = setup(
        ctx, "--client", client, "--at", f"{client}=other client", "--json"
    )
    assert code == 0, err
    row = json.loads(out)["clients"][0]
    for kind in ("mcp", "hooks"):
        assert Path(row[kind]["config_path"]).is_relative_to(base)
    assert json.loads(out)["searched"] == [
        {"client": client, "directory": str(base), "installed": True}
    ]
    assert list(base.iterdir()) == []
    assert ctx.client_dirs == {} and ctx.env == {}
    assert setup(ctx, "--client", client, "--json")[0] == 1


def test_explicit_location_overrides_native_environment_and_reaches_cli(
    tmp_path: Path,
) -> None:
    ctx = replace(context(tmp_path), env={"CODEX_HOME": str(tmp_path / "old")})
    base = tmp_path / "new"
    base.mkdir()
    seen: list[str] = []

    def runner(_argv: object, env: object) -> tuple[int, str]:
        assert isinstance(env, dict)
        seen.append(env["CODEX_HOME"])
        return 0, ""

    code = run_setup(
        ["--write", "--client", "codex", "--at", f"codex={base}"],
        {"NEOSIAN_HOME": str(tmp_path / "store")},
        context=ctx,
        out=io.StringIO(),
        err=io.StringIO(),
        runner=runner,
    )
    assert code == 0 and seen == [str(base)]
    assert (base / "hooks.json").exists()
    assert ctx.env["CODEX_HOME"].endswith("old")


def test_missing_explicit_location_is_not_created(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    missing = tmp_path / "missing"
    code, out, _ = setup(
        ctx, "--write", "--client", "pi", "--at", f"pi={missing}", "--json"
    )
    assert code == 1 and not missing.exists()
    assert json.loads(out)["searched"][0]["directory"] == str(missing)


@pytest.mark.parametrize(
    "values", [["cursor"], ["unknown=place"], ["pi="], ["pi=a", "pi=b"]]
)
def test_invalid_locations_refuse_before_writing(
    tmp_path: Path, values: list[str]
) -> None:
    args = [arg for value in values for arg in ("--at", value)]
    code, _, err = setup(context(tmp_path), "--write", "--yes", *args)
    assert code == 2 and "--at" in err


async def test_status_reports_same_override_and_default_searches(
    tmp_path: Path,
) -> None:
    ctx = context(tmp_path)
    base = tmp_path / "custom"
    base.mkdir()
    assert setup(ctx, "--client", "cursor", "--at", f"cursor={base}", "--write")[0] == 0
    out, err = io.StringIO(), io.StringIO()
    code = await run_status(
        ["--json", "--at", f"cursor={base}"],
        {"NEOSIAN_HOME": str(tmp_path / "store")},
        context=ctx,
        out=out,
        err=err,
    )
    assert code == 0, err.getvalue()
    rows = {r["client"]: r for r in json.loads(out.getvalue())["clients"]}
    cursor = rows["cursor"]
    assert cursor["installed"] and cursor["mcp_registered"] and cursor["hooks_present"]
    assert cursor["searched_directory"] == str(base)
    assert rows["pi"]["searched_directory"] == str(tmp_path / ".pi" / "agent")


def test_print_reports_searched_directories_without_creating_them(
    tmp_path: Path,
) -> None:
    ctx = context(tmp_path)
    code, out, err = setup(ctx, "--json")
    assert code == 1 and len(json.loads(out)["searched"]) == len(CLIENTS)
    assert "searched" in err and not (tmp_path / ".pi").exists()
