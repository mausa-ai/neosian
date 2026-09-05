"""The console script (NF, TP-2).

`neosian` is the shell: typer, rich and the terminal menu ride the `cli`
extra, so the core install answers with the install hint, never a
traceback. The `python -m` doors — `neosian.memory`, `neosian.record`,
`neosian.mcp`, `neosian.server`, `neosian.ledger`, `neosian.mobility` —
are argparse and need no extra.
"""

import sys

_INSTALL_HINT = (
    "The neosian shell requires the 'cli' extra — "
    "uv tool install 'neosian[cli]' (or pip install 'neosian[cli]')"
)


def main() -> None:
    """Run the shell, or print the missing-extra hint at exit 1."""
    try:
        import typer  # noqa: F401  # the extra's marker
    except ImportError:
        print(f"error: {_INSTALL_HINT}", file=sys.stderr)
        raise SystemExit(1) from None
    from neosian._cli.main import app

    app()
