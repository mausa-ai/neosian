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

The shipped pack `examples/eval_memory_baseline.yaml` — six scenarios,
one per behavior:

| Scenario | Behavior |
|---|---|
| write-discipline | Facts filed in the right mount, one document per topic, a refused secret never stored |
| recall-next-session | A later session reads the fact back (fresh index, `view`, no rewrite) instead of guessing |
| dedup | A second fact on the same topic updates the existing document — never a near-duplicate |
| contradiction | A reversed fact is updated and the stale value survives in no live text |
| long-horizon-recall | The session-1 fact survives two unrelated writing sessions and is recalled in session 4 |
| correct-wrong-memory | A disavowed note is deleted — the claim gone from every live document |

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
- One model per provider, the same four the library's external tier
  pins: `claude-sonnet-5`, `gpt-5-mini-2025-08-07`,
  `openai/gpt-oss-120b` (Groq), `gpt-oss-120b` (Cerebras).
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

The results below were measured against exactly these files. A unit
test (`tests/unit/test_baselines.py`) fails when either file changes
without this section being updated — **no prompt-pack change without a
recorded baseline re-run.**

- `neosian/assets/prompts/memory.yaml` — sha256
  `0840039c93a763d3b2889729f6338153b9799491110db8c7894a55bf62a248fd`
- `examples/eval_memory_baseline.yaml` — sha256
  `f9b8e2cd76bd545867225e37233cf220bd9e0badb7f0e898b51baec0d4669dad`

## Results

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
| Groq | openai/gpt-oss-120b | 3/6 † | 3/6 † | n/a |
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
  "PostgreSQL 16", "the \`main\` branch" — literal `contains` pins
  became stems or regexes.
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
  signals underneath: Groq's model failed to emit a valid tool call in
  one function-transport cell, and Anthropic's native transport first
  tried its trained `/memories/…` path prefix, hit the corrective
  failure, and recovered in-turn.
- Standing schedule: the `external` CI jobs run weekly (Mondays 06:00
  UTC, `.github/workflows/ci.yml`) per provider with repo-secret keys
  (set 2026-08-21); an absent secret self-skips.
