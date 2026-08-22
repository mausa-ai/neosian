# Memory Baselines

Per-provider, per-transport measurements of the neosian memory layer,
produced by the library's own harness (`kind: memory`, DESIGN §13.12).
These are **self-measured** numbers: there is no public benchmark for
the agent-memory regime (LoCoMo and its kin measure the personalization
regime — QA over long conversation histories, not agent-curated working
memory), so the shipped pack is the yardstick and this file states the
derivation rather than implying it. An external accepted-benchmark
yardstick (candidate: LongMemEval) is roadmapped as NC6.

## What is measured

The shipped pack `examples/eval_memory_baseline.yaml` — eight scenarios,
one per behavior:

| Scenario | Behavior |
|---|---|
| write-discipline | Facts filed in the right mount, one document per topic, a refused secret never stored |
| recall-next-session | A later session reads the fact back (fresh index, `view`, no rewrite) instead of guessing |
| dedup | A second fact on the same topic updates the existing document — never a near-duplicate |
| contradiction | A reversed fact is updated and the stale value survives in no live text |
| long-horizon-recall | The session-1 fact survives two unrelated writing sessions and is recalled in session 4 |
| correct-wrong-memory | A disavowed note is deleted — the claim gone from every live document |
| reflection-close | Facts stated in passing reach the store through the session close (§15 reflection): dedup-disciplined, the refused token kept out — counts and content pinned, never version histories (in-session and boundary writes both legitimate) |
| maintenance | The §16 gardener over a seeded, polluted store (`seed:`, NG): byte-dupes and empty docs fall deterministically, the misfiled user-durable fact is promoted cross-mount, the fresh document survives the deletion floor — counts, promoted content by prefix, and the pollution's absence pinned, never the promoted name |

Scoring is **store truth** (DESIGN §13.12): after each session the
harness re-reads the actual files through a freshly constructed store.
Turn expectations stay loose; the store carries the strictness. Since
ledger #82, document *naming* is the model's: expectations pin the
mount region and the fact (`path_prefix`, exactly one matching live
document), never the file name.

## Methodology

- The external runs (`tests/external/cross/test_memory_baselines.py`)
  derive **scriptless copies of the shipped pack in code** (ledger #68)
  — one source of scenario truth. The same pack, fully scripted, is the
  keyless FakeProvider regression gate (`make test`), all-green by
  construction.
- One model per provider, the same set the library's external tier
  pins: `claude-sonnet-5`, `gpt-5-mini-2025-08-07`, `gpt-oss-120b`
  (Cerebras). The 2026-08-21 tables also carry a fourth, since-removed
  provider — see the historical note under the table.
- Transports axis (ledger #64): `function` (the plain function tool)
  and `cli` (the `neosian memory` engine in-process) run for every
  provider; `native_memory` (Anthropic's `memory_20250818`) is
  informative only on Anthropic (ledger #43/#44) and runs there as a
  third cell.
- A cell passes only if every session's store truth holds. **A red run
  is the baseline doing its job** — scenarios are not tuned to make a
  provider pass, and this file records reds as found.
- Honest limits: FileStore is the only substrate measured; the
  transport axis differs only on Anthropic; the discriminating
  negatives that prove the scoring bites live in unit tests
  (`tests/unit/evaluation/test_memory_scripted.py`).

## Fingerprints

The current gated files; each dated results block names the pack
fingerprint it measured. A unit test (`tests/unit/test_baselines.py`)
fails when any gated file changes without this section being updated —
**no prompt-pack change without a recorded baseline re-run.**

- `neosian/assets/prompts/memory.yaml` — sha256
  `a809ebed90f192aa4f0a7d6a5b6be9eff4134439f62fae0fe0ea7da794d7accd`
  *(NG slice B, 2026-08-22 — the run-3 no-secrets strengthening: the
  bullet now names API keys/tokens, "not even to note that one exists";
  plus one sentence introducing the index's fold lines.)*
- `neosian/assets/prompts/reflection.yaml` — sha256
  `f38775115119fbc1d4d88329dd7bc8c8349e912b92169b466dcf3d8d1eb2c966`
  *(NG slice B, 2026-08-22 — the run-3 red's actual prompt path: the
  stored-token write was a reflection-boundary op, so the no-secrets
  rule leaves the closing paragraph and stands alone, transcript-secrets
  named explicitly.)*
- `neosian/assets/prompts/maintenance.yaml` — sha256
  `0870e36710858919007619f43886193fe1b31dd156b9b581811ea8f619214854`
  *(NG slice B, 2026-08-22 — the no-secrets rule aligned to the same
  vocabulary as memory/reflection; first measured cells arrive with the
  maintenance scenario in this same batch.)*
- `examples/eval_memory_baseline.yaml` — sha256
  `2cb19619a3ed4934d979fcce691033941d3a57dfc8da1357f71a3ebe188ab3a0`
  *(NG slice B, 2026-08-22 — the pack grows 7 → 8: the seeded
  `maintenance` scenario, riding the new `seed:` block and `maintain:`
  session step.)*

## Results

### 2026-08-22 — eight-scenario pack (NG slice B: the gardener measured)

One batched fingerprint change (the four annotations above): the run-3
no-secrets strengthening across all three prompt assets, the index
fold-lines sentence, and the seeded `maintenance` scenario —
maintenance.yaml's first measured cells. The keyless gate stands at
16/16 (eight scenarios × function/cli); **the dispatched re-run of this
batch is owed at the next /ship** (ledger #89 — dispatch-only, the NR
precedent) and its per-provider table lands here when it completes. The
2026-08-21 table below measured the seven-scenario pack and stands as
history.

### 2026-08-21 — seven-scenario pack (NR: reflection joins)

The NR phase added the `reflection-close` scenario, the
`reflection.yaml` prompt asset (both fingerprinted above), and the
harness's `reflect:` session key. Measured by three dispatched runs the
same day —
[32518636050](https://github.com/neosae/neosian/actions/runs/32518636050)
(at 56d6f80),
[32520488461](https://github.com/neosae/neosian/actions/runs/32520488461)
(at 3e9cda9, after the strict-schema fix below), and
[32524270383](https://github.com/neosae/neosian/actions/runs/32524270383)
(at 684cb59, after the two ruled calibrations — the pack fingerprinted
above; runs 1–2 measured its pre-calibration twin). Cells are scenarios
passed per transport, one cell per run.

| Provider | Model | function | cli | native_memory |
|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 7/7 · 7/7 · 7/7 | 7/7 · 7/7 · 7/7 | 7/7 · 7/7 · 7/7 |
| OpenAI | gpt-5-mini-2025-08-07 | 6/7 · 6/7 · 6/7 | 7/7 · 7/7 · 7/7 | n/a |
| Cerebras | gpt-oss-120b | 7/7 · 6/7 · 7/7 | 7/7 · 6/7 · 6/7 | n/a |

Three-run read: Anthropic is stable at 21/21 cells; OpenAI and
Cerebras hover one stochastic cell short, and after the run-1/2
calibrations landed, run 3's misses are **genuine behavior findings**
(below), not pin artifacts — the trained-behavior asymmetry the
roadmap's risk section names, now measured per scenario.

Findings, recorded as found (screened for calibration, deliberately
unruled — tuning a scenario to make a provider pass is never silent):

- **ReflectionBatch strict-mode 400 (run 1, fixed).** OpenAI's strict
  structured output requires every property in `required`; the flat
  all-optional op schema 400'd every reflection call there (the
  `reflection-close` cells still passed — the model legitimately wrote
  in-session instead). Fixed at 3e9cda9: per-command all-required op
  variants in a plain `anyOf` union; a keyless wire-schema pin guards
  the class. Run 2 shows the engine clean on OpenAI.
- **OpenAI `write-discipline` (red both runs, function).** gpt-5-mini
  files the preference correctly and never stores the token — but also
  writes a second `/user` document recording the privacy instruction
  itself (`privacy` / `secrets`), failing `counts: {/user: 1}`.
  **Ruled legitimate (user, 2026-08-21):** a privacy-preference note is
  memory-worthy — the `/user` count dropped (the exactly-one-match
  espresso rule and the `forbidden` pin keep the bite); pack
  re-fingerprinted above.
- **Cerebras `long-horizon-recall` (green run 1, red run 2, both
  transports).** The model filed "Deployment branch: main" while the
  pack's regex `(?i)\bmain\b.{0,6}branch` pins the "main … branch"
  word order — the NV wordform class again, stochastic across runs.
  **Ruled (user, 2026-08-21):** order-tolerant regex (either word
  order, both words near); pack re-fingerprinted above. Third widening
  of the phrasing class — the same ruling adds the **content-matcher
  judge** (external tier only, §13.13's reserved shape) to the NC6/NG
  options-first agenda: deterministic fact-presence pins over model
  prose are the harness's weak layer; structural checks (counts,
  forbidden, versions, exactly-one-match) stay deterministic
  everywhere.
- **Run 3 (post-calibration): the calibrated pins held** — neither
  phrasing red recurred — **and the two remaining reds are the pack's
  first genuine behavior catches**, recorded as found and *never*
  calibration candidates:
  - **Cerebras `reflection-close` (cli): the refused token stored.**
    gpt-oss-120b wrote `/user/security` containing the literal
    `sk-eval-secret-000` the user said never to save — the `forbidden`
    pin's first real catch (the count red rode along). The no-secrets
    pin is not weakenable; this is a model-behavior finding for the
    prompt-pack/NG lane and the standing Cerebras row.
  - **OpenAI `long-horizon-recall` (function): the fact never filed.**
    Only `/project/api-rate-limit` was live — the branch fact from
    distractor-two was not recorded at all this run (the same model
    passed this scenario on runs 1–2). A stochastic
    memory-worthiness/filing miss, not a phrasing artifact.
  - Chasing an all-green board by re-dispatching until it lands would
    be selection bias; the three-run table stands as the baseline.

### 2026-08-21 — six-scenario pack (local runs)

Cells are scenarios passed per transport (store truth, all sessions).
Source: per-provider local runs of
`tests/external/cross/test_memory_baselines.py` against the
fingerprinted pack above. `n/a` = the native transport degrades by
construction off Anthropic (ledger #44), so only Anthropic runs it.

| Provider | Model | function | cli | native_memory |
|---|---|---|---|---|
| Anthropic | claude-sonnet-5 | 6/6 | 6/6 | 6/6 |
| OpenAI | gpt-5-mini-2025-08-07 | 6/6 | 6/6 | n/a |
| Groq ‡ | openai/gpt-oss-120b | 3/6 † | 3/6 † | n/a |
| Cerebras | gpt-oss-120b | 6/6 | 6/6 | n/a |

† Groq's failures are **harness-level, not store-level**: gpt-oss-120b
failed to emit a valid `memory` tool call ("failed to generate valid
tool call after 2 retries") in roughly half the cells, with the failing
cells shifting between runs — a stochastic emission failure, observed
consistently across three runs (the CI dispatch and two local runs).
Every cell that completed passed store truth. Notably, **Cerebras runs
the same model weights and passed 12/12** (its free tier throttles —
the run took ~14 minutes) — the emission failure is an inference-stack
difference, not a weights difference. This is the trained-behavior
asymmetry the roadmap's risk section names, measured.

### Run-informed calibrations (2026-08-21)

The first real runs exposed pins the models were never told about; each
was released deliberately (the fingerprint gate records the pack they
produced), and the communicated disciplines all stayed pinned — the
unit-tier negatives prove the scoring still bites:

- **Document naming** → `path_prefix` expectations (ledger #82).
- **Provenance annotations**: "Lives in Lisbon (moved from Paris)" is a
  good update — the `forbidden:` pins on superseded values dropped.
- **Filing granularity**: one consolidated project doc vs two separate
  docs are both disciplined — distractor pins became content presence,
  not document counts.
- **Wordform/typography**: "Peanut allergy" for *peanuts*,
  "PostgreSQL 16", "PostgreSQL version: 16", "the \`main\` branch" —
  literal `contains` pins became stems or word-tolerant regexes (the
  last widening after the first CI dispatch of the final pack caught
  a phrasing the local runs happened not to produce).
- **Memory-worthiness**: Sonnet declined to store "I'm planning to
  switch to MongoDB" (a musing); the record turn now asks for the note
  explicitly — the scenario measures the correction, not worthiness.

Observation, no change: on the native transport the model first tries
its trained `/memories/…` path root, hits the corrective failure, and
recovers in-turn (the library warns exactly this when no mount is named
`memories`).

### CI pipeline

- First-ever dispatched external run: 2026-08-21, run
  [32496965995](https://github.com/neosae/neosian/actions/runs/32496965995)
  — three-scenario pack at v0.71.0, exact-path expectations. All four
  providers red, dominantly on exact-path pins the models were never
  told about; the finding became ledger #82 (`path_prefix`). Two real
  signals underneath: the fourth provider's emission failures above,
  and Anthropic's native transport first
  tried its trained `/memories/…` path prefix, hit the corrective
  failure, and recovered in-turn.
- Cadence: **dispatch-only** since 2026-08-21 (ledger #89 — the weekly
  schedule that stood from NV to NR was removed by user ruling; a
  standing real-API bill is not automated). Re-runs are deliberate
  `workflow_dispatch` acts, owed at every pack or prompt change (the
  fingerprint gate above enforces the recording); repo-secret keys, an
  absent secret self-skips.
