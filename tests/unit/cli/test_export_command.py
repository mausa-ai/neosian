"""`neosian export` is a verbatim pass-through to the one argv grammar."""

import click
import pytest
import typer

import neosian.mobility as mobility_module
from neosian._cli.main import export


def test_arguments_forward_verbatim_behind_the_verb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake_main(argv: list[str] | None = None, *, prog: str = "") -> int:
        del prog
        assert argv is not None
        seen.append(argv)
        return 0

    monkeypatch.setattr(mobility_module, "main", fake_main)
    ctx = click.Context(click.Command("export"))
    ctx.args = ["archive", "--root", "m", "--scope", "user:me"]
    with pytest.raises(typer.Exit) as excinfo:
        export(ctx)  # type: ignore[arg-type]
    assert excinfo.value.exit_code == 0
    assert seen == [["export", "archive", "--root", "m", "--scope", "user:me"]]


def test_exit_code_is_the_return_value(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_main(argv: list[str] | None = None, *, prog: str = "") -> int:
        del argv, prog
        return 2

    monkeypatch.setattr(mobility_module, "main", failing_main)
    ctx = click.Context(click.Command("export"))
    ctx.args = []
    with pytest.raises(typer.Exit) as excinfo:
        export(ctx)  # type: ignore[arg-type]
    assert excinfo.value.exit_code == 2
