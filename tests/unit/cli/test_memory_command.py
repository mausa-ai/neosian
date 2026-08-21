"""`neosian memory` is a verbatim pass-through to the one argv grammar."""

import click
import pytest
import typer

import neosian.memory.cli as memory_cli_module
from neosian._cli.main import memory


def test_arguments_forward_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_main(argv: list[str] | None = None, *, prog: str = "") -> int:
        del prog  # the fake ignores it
        assert argv is not None
        seen.append(argv)
        return 0

    monkeypatch.setattr(memory_cli_module, "main", fake_main)
    ctx = click.Context(click.Command("memory"))
    ctx.args = ["create", "/memories/a b", "--content", "-", "--actor", "cli:x"]
    with pytest.raises(typer.Exit) as excinfo:
        memory(ctx)  # type: ignore[arg-type]
    assert excinfo.value.exit_code == 0
    assert seen == [["create", "/memories/a b", "--content", "-", "--actor", "cli:x"]]


def test_exit_code_is_the_return_value(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_main(argv: list[str] | None = None, *, prog: str = "") -> int:
        del argv, prog  # the fake ignores them
        return 2

    monkeypatch.setattr(memory_cli_module, "main", failing_main)
    ctx = click.Context(click.Command("memory"))
    ctx.args = []
    with pytest.raises(typer.Exit) as excinfo:
        memory(ctx)  # type: ignore[arg-type]
    assert excinfo.value.exit_code == 2
