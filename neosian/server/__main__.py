"""`python -m neosian.server` — the module spelling of `neosian serve`."""

from neosian.server.serve import main

raise SystemExit(main(prog="python -m neosian.server"))
