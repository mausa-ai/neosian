"""`neosian search` is a verbatim pass-through to the one argv grammar."""

import click
import pytest
import typer

import neosian.search as search_module
from neosian._cli.main import search


def test_arguments_forward_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_main(argv: list[str] | None = None, *, prog: str = "") -> int:
        del prog
        assert argv is not None
        seen.append(argv)
        return 0

    monkeypatch.setattr(search_module, "main", fake_main)
    ctx = click.Context(click.Command("search"))
    ctx.args = ["cache", "ttl", "--root", "m", "--conversation", "cc-1"]
    with pytest.raises(typer.Exit) as excinfo:
        search(ctx)  # type: ignore[arg-type]
    assert excinfo.value.exit_code == 0
    assert seen == [["cache", "ttl", "--root", "m", "--conversation", "cc-1"]]


def test_exit_code_is_the_return_value(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_main(argv: list[str] | None = None, *, prog: str = "") -> int:
        del argv, prog
        return 2

    monkeypatch.setattr(search_module, "main", failing_main)
    ctx = click.Context(click.Command("search"))
    ctx.args = []
    with pytest.raises(typer.Exit) as excinfo:
        search(ctx)  # type: ignore[arg-type]
    assert excinfo.value.exit_code == 2
