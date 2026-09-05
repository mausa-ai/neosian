# Contributing

neosian is small on purpose: an async-only, stateless agent core with
opt-in state layers, held to `mypy --strict`, one contract per seam, and
"not one line that is not needed". Contributions that keep it that way
are welcome.

## The loop

```bash
make install    # uv sync --locked --all-groups
make lint       # ruff + black --check + import-linter
make typecheck  # mypy --strict over neosian, tests and examples
make test       # the unit tier — zero API keys, the coverage floor
make size       # file-size gate (warn 300 / fail 500 lines)
```

All four gates are green on every commit, with no API key set. The
unit tier runs on `FakeProvider` and the shipped fakes; real-API suites
are `external_<provider>` markers, dispatched deliberately, never a
standing cost. `make test-postgres` and `make test-container` cover the
Postgres store and the state-process container when you have them.

## Before you write

- [docs/DESIGN.md](docs/DESIGN.md) says *how*; its §12 ledger records
  every deliberate departure. A change that departs from a documented
  decision adds a ledger row in the same change, never a silent
  divergence.
- [docs/ECOSYSTEM.md](docs/ECOSYSTEM.md) holds the frozen host-facing
  seams (scope grammar, token classes, integer micro-USD, the event
  vocabulary, error codes). Error codes are append-only; a seam change
  is a governed move, not a pull request.
- [docs/ROADMAP.md](docs/ROADMAP.md) says *in what order*; open a
  discussion before a feature the roadmap does not name.

Rules that are never relaxed: async-only (no `asyncio.run` in library
code); the `Agent` stores nothing between calls; every shipped prompt
is data under `neosian/assets/`; money is integer micro-USD with
ceiling division; timestamps are tz-aware UTC; the public API is pinned
by `tests/unit/test_init.py`, so an export change is a reviewed diff.

## Pull requests

- One coherent outcome per commit. Subject: `<ID>: <what became true>`
  (a roadmap phase id, or `meta`), at most 72 characters; the body
  carries the why, the DESIGN section, and how it was verified.
- Tests ride alongside the change: prefer the shipped fakes over
  ad-hoc mocks; per-test skip helpers, never module-level skips.
- Keep prose in the wheel's docs pages (`neosian docs`) and the README
  short; the tour and the design documents are the long form.
- Sign off every commit (`git commit -s`). Contributions are accepted
  under the [Developer Certificate of Origin](https://developercertificate.org/)
  and licensed as the project is, Apache-2.0. There is no CLA.

Security reports go to [SECURITY.md](SECURITY.md), not to an issue.
Conduct in every project space follows
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
