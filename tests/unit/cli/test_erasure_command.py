"""`neosian redact` and `neosian prune` are verbatim pass-throughs to the
one argv grammar, the verb first."""

import click
import pytest
import typer

import neosian.erasure as erasure_module
from neosian._cli.main import prune, redact


@pytest.mark.parametrize(
    ("command", "verb", "args"),
    [
        (redact, "redact", ["ses_1", "--all", "--root", "m"]),
        (prune, "prune", ["--older-than", "30d", "--dry-run"]),
    ],
)
def test_arguments_forward_verbatim_behind_the_verb(
    monkeypatch: pytest.MonkeyPatch, command: object, verb: str, args: list[str]
) -> None:
    seen: list[list[str]] = []

    def fake_main(argv: list[str] | None = None, *, prog: str = "") -> int:
        del prog
        assert argv is not None
        seen.append(argv)
        return 0

    monkeypatch.setattr(erasure_module, "main", fake_main)
    ctx = click.Context(click.Command(verb))
    ctx.args = args
    with pytest.raises(typer.Exit) as excinfo:
        command(ctx)  # type: ignore[operator]
    assert excinfo.value.exit_code == 0
    assert seen == [[verb, *args]]


def test_exit_code_is_the_return_value(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_main(argv: list[str] | None = None, *, prog: str = "") -> int:
        del argv, prog
        return 2

    monkeypatch.setattr(erasure_module, "main", failing_main)
    ctx = click.Context(click.Command("redact"))
    ctx.args = []
    with pytest.raises(typer.Exit) as excinfo:
        redact(ctx)  # type: ignore[arg-type]
    assert excinfo.value.exit_code == 2
