"""`neosian record` is a verbatim pass-through to the one argv grammar."""

import click
import pytest
import typer

import neosian.record as record_module
from neosian._cli.main import record


def test_arguments_forward_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_main(argv: list[str] | None = None, *, prog: str = "") -> int:
        del prog
        assert argv is not None
        seen.append(argv)
        return 0

    monkeypatch.setattr(record_module, "main", fake_main)
    ctx = click.Context(click.Command("record"))
    ctx.args = [
        "install",
        "--client",
        "claude-code",
        "--root",
        "m",
        "--scope",
        "user:me",
    ]
    with pytest.raises(typer.Exit) as excinfo:
        record(ctx)  # type: ignore[arg-type]
    assert excinfo.value.exit_code == 0
    assert seen == [
        ["install", "--client", "claude-code", "--root", "m", "--scope", "user:me"]
    ]


def test_exit_code_is_the_return_value(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_main(argv: list[str] | None = None, *, prog: str = "") -> int:
        del argv, prog
        return 2

    monkeypatch.setattr(record_module, "main", failing_main)
    ctx = click.Context(click.Command("record"))
    ctx.args = []
    with pytest.raises(typer.Exit) as excinfo:
        record(ctx)  # type: ignore[arg-type]
    assert excinfo.value.exit_code == 2
