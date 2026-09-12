"""The console script (NF, TP-2; the shell core since NX, ledger #206).

`neosian` is the shell: typer and rich ship in the install, and an
environment missing them answers with the reinstall hint, never a
traceback. The `python -m` doors — `neosian.memory`,
`neosian.record`, `neosian.mcp`, `neosian.server`, `neosian.ledger`,
`neosian.mobility` — are argparse and need none of them.
"""

import json
import sys

_INSTALL_HINT = (
    "The neosian shell needs typer, which the neosian install carries — "
    "reinstall: uv tool install neosian (or pip install neosian)"
)


def main() -> None:
    """Run the shell, or print the reinstall hint at exit 1."""
    try:
        import typer  # noqa: F401  # the shell's marker
    except ImportError:
        print(f"error: {_INSTALL_HINT}", file=sys.stderr)
        raise SystemExit(1) from None
    from neosian._cli.config import ConfigFileError
    from neosian._cli.main import app

    try:
        app()
    except ConfigFileError as exc:  # any verb that reads the keys (EC-11)
        if "--json" in sys.argv:
            print(json.dumps({"error": exc.message, "hint": None}))
        print(f"error: {exc.message}", file=sys.stderr)
        raise SystemExit(1) from None
