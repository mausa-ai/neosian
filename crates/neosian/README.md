# neosian

The state layer for the agent you already use, as one static binary:
the record, the memory server and the state process, written a second
time in Rust so a hook costs single-digit milliseconds. The Python SDK
and shell are `pip install neosian`; both answer to the name `neosian`.

`neosian setup`, `status`, and `configure` use the same home as the Python
shell. Setup connects Claude Code, Codex, OpenCode, Muse Code, Cursor and
Pi, and registers this binary by default. `--write --yes` applies all
detected clients; repeated `--client` and `--at CLIENT=DIR` narrow the work.

`mcp install` and `record install` print or apply a single client's
registration. `status` names competing executables on PATH. `update`
checks GitHub Releases; `--write` calls the adjacent cargo-dist updater
only for the installation its receipt owns, within the installed major.
The home and recorded state require no migration.

Documentation: <https://neosian.com/docs>. Source and design:
<https://github.com/mausa-ai/neosian>.
