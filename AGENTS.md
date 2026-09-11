# neosian — conventions for agents and people

Async-only Python library: the state layer for LLM agents — a stateless
`Agent` core (tools, orchestration, streaming, fallback, guardrails,
structured output) with opt-in `Conversation` + memory layers around it,
on storage the product owns. Read `README.md` first; `neosian docs
<topic>` prints the shipped pages from the wheel.

## Hard rules

- **Async-only.** No sync wrapper, no `asyncio.run()` in library code — it
  deadlocks in notebooks and servers.
- **Stateless core.** `Agent` stores nothing between calls; `Conversation`
  and memory are opt-in layers, never state smuggled in.
- **Prompts are data.** Every shipped prompt (guardrail policies, tool
  descriptions, the memory prompt pack) lives in `neosian/assets/` as YAML
  or markdown + frontmatter — never prose in Python.
- **Money = integer micro-USD** (ceiling division, never undercount);
  floats only at display edges. **Timestamps are tz-aware UTC.**
- **The ABC is the contract.** Storage ABCs own no connection, no commit,
  no DDL; `FileStore`/`PostgresStore`/`RemoteStore` are reference
  implementations, kept honest by the shipped conformance kits.
- **The public API is `__all__`-pinned** by `tests/unit/test_init.py` —
  every export change is a deliberate, reviewed diff.
- **Seams are frozen; error codes are append-only.** Scope grammar, token
  classes, integer µ$, the event vocabulary, the error-code families and
  the wire (`WIRE_VERSION`) change only by a recorded decision, never in
  passing.
- **Never name a host.** No host imports, no host assumptions (async DB,
  web framework, tenancy).

## Conventions

- `mypy --strict` passes, always. `ruff` + `black`, line length 88. Simple
  elegant quality code — not one line that is not needed.
- Constants live module-local; `shared/constants.py` is named debt, never
  grown further — new constants go next to their use.
- File-size gate: warn 300, fail 500 lines (`make size`); allowlist
  entries carry a ceiling and a reason, stale entries fail the gate.
- Test tiers: `unit` (FakeProvider and the shipped fakes only — the
  default, fully keyless) / `external_<provider>` (real API calls;
  dispatch-only). Per-test skip helpers, never module-level skips; key
  checks test falsiness (an absent CI secret is `""`). Prefer the shipped
  fakes over ad-hoc `AsyncMock`.
- Import layering is enforced by import-linter: `_foundation` never
  imports `_cli`; memory/conversation never import provider internals;
  the facade only re-exports.
- `llms.txt` and `neosian/assets/llms.txt` are byte-identical twins; the
  docs pages under `neosian/assets/docs/` are a manifest — a page not
  listed fails at import.

## Gates

| Command | What |
|---|---|
| `make install` | `uv sync --locked --all-groups` |
| `make lint` / `make format` | ruff + black --check + lint-imports / autofix |
| `make typecheck` | `mypy --strict neosian tests examples` |
| `make test` | unit tier — the default gate, zero API keys, the coverage floor |
| `make size` | file-size gate (300/500) |
| `make test-external provider=<p> [file=…]` | real-API suite for one provider; `file=` injects creds value-blind |
| `make test-postgres` | PostgresStore suite; needs `NEOSIAN_TEST_POSTGRES_DSN` (`SERVICES.md`) |
| `make test-container` | the state-process image against both conformance kits (docker) |

All four default gates green, with no API key set, before every commit.
`SERVICES.md` lists every environment key and what turning it off means.

## Versioning and releases

The version literal lives once, in `pyproject [project].version`;
`__version__` derives via `importlib.metadata` (a unit test pins it).
Release axis = annotated `v<X.Y.Z>` tags, cut by `make release v=X.Y.Z`
on a clean tree with the version's `## [X.Y.Z]` section in
`CHANGELOG.md`; pushing a `v*` tag runs `release.yml` (PyPI by trusted
publishing, the image to GHCR). Bump on public-surface change, not per
commit. `[Unreleased]` in `CHANGELOG.md` accumulates until the bump.

## Commits

- Subject ≤ 72 chars, outcome not mechanics: `<what became true>`,
  prefixed by the phase id when the work belongs to one, `meta:` for
  housekeeping.
- Body when it matters: the *why*; `Docs: <section>` where a documented
  decision is touched; `Verified: <how>` (or explicitly `not verified`).
- Sign off every commit (`git commit -s`, the DCO). No attribution
  trailers. One commit per coherent outcome.
- Push `--follow-tags`.
