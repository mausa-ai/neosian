"""`neosian mcp` is a verbatim pass-through to the one argv grammar."""

import click
import pytest
import typer

import neosian.mcp.serve as serve_module
from neosian._cli.main import mcp


def test_arguments_forward_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_main(argv: list[str] | None = None) -> int:
        assert argv is not None
        seen.append(argv)
        return 0

    monkeypatch.setattr(serve_module, "main", fake_main)
    ctx = click.Context(click.Command("mcp"))
    ctx.args = ["--root", "m", "--scope", "user:me", "--actor", "a b"]
    with pytest.raises(typer.Exit) as excinfo:
        mcp(ctx)  # type: ignore[arg-type]
    assert excinfo.value.exit_code == 0
    assert seen == [["--root", "m", "--scope", "user:me", "--actor", "a b"]]


def test_exit_code_is_the_return_value(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_main(argv: list[str] | None = None) -> int:  # noqa: ARG001 - fake
        return 2

    monkeypatch.setattr(serve_module, "main", failing_main)
    ctx = click.Context(click.Command("mcp"))
    ctx.args = []
    with pytest.raises(typer.Exit) as excinfo:
        mcp(ctx)  # type: ignore[arg-type]
    assert excinfo.value.exit_code == 2
