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

`memory`, `messages`, `search`, `continue`, `audit`, `export`, `import`,
`redact` and `prune` work on files, Postgres or either state process through
`--url`. Recording and MCP use those same backends; `serve` hosts files or
Postgres. The database schema stays operator-owned. Chat, playground, eval
and `memory maintain` name the Python shell. `neosian docs cli` has the
complete command table.

Install with `curl -fsS https://neosian.com/install | sh`, or use the
PowerShell installer/MSI on the GitHub Release. Publisher signing and
Apple notarization are deferred; browser downloads can trigger warnings.
The binary's package-manager commands use npm (`npm install -g neosian`)
and Cargo (`cargo install neosian --locked`). Homebrew distribution is
deferred. Update those installations through their package manager.
Cargo builds from source and requires the crate's declared Rust version.

To prepare a source archive, start with a clean repository, run
`python3 scripts/package_crate.py` then `cargo package --package neosian
--locked --allow-dirty`. The staged assets are generated from
`neosian/assets/` and ignored by Git; Cargo requires that flag to include
them. CI and publication check the source tree is clean before staging.
Registry builds need no Python and fetch no runtime data.

Documentation: <https://neosian.com/docs>. Source and design:
<https://github.com/mausa-ai/neosian>.
