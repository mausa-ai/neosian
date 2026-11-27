"""The records memory gate measures what it declares and fails what it should.

Expected values are written here from DESIGN §40.54 item 16, not read from the
script, so a dropped case, a loosened bound or a changed budget fails a test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts import bench_records_memory as bench
from scripts.bench_records_memory import Case, GateFailure, Measured

ROOT = Path(__file__).resolve().parents[2]
MIB = 1 << 20
TURNS = (1, 4, 16)
CONVERSATIONS = (16, 32, 64)
# (operation, series, sizes in MiB or records) -> budget in MiB
DECLARED: dict[tuple[str, str], tuple[tuple[int, ...], float]] = {
    ("write part", "result"): (TURNS, 24.0),
    ("write part", "messages"): (TURNS, 24.0),
    ("seal", "result"): (TURNS, 16.0),
    ("seal", "messages"): (TURNS, 16.0),
    ("write tiny part", "tiny"): ((2048,), 24.0),
    ("piece fetch", "result"): (TURNS, 16.0),
    ("restore part", "result"): (TURNS, 24.0),
    ("restore part", "messages"): (TURNS, 24.0),
    ("restore part", "conversation"): (CONVERSATIONS, 24.0),
    ("commit", "result"): (TURNS, 16.0),
    ("commit", "messages"): (TURNS, 16.0),
    ("commit", "conversation"): (CONVERSATIONS, 16.0),
    ("read full", "conversation"): (CONVERSATIONS, 16.0),
    ("read light", "conversation"): (CONVERSATIONS, 16.0),
    ("search newest", "conversation"): (CONVERSATIONS, 16.0),
    ("search relevance", "conversation"): (CONVERSATIONS, 16.0),
    ("sweep preview", "result"): (TURNS, 16.0),
}


# Ruling K1: on PostgreSQL every budget adds 4 pooled connections x 4 MiB.
POOLED = {24.0: 40.0, 16.0: 32.0}
# Warm PostgreSQL peaks recorded on 2026-11-28 (MiB, 1/4/16 MiB or 16/32/64).
RECORDED = {
    ("write part", "result"): (27.9, 29.6, 29.6),
    ("write part", "messages"): (27.9, 29.6, 29.7),
    ("seal", "result"): (27.5, 27.5, 28.7),
    ("read full", "conversation"): (28.4, 29.9, 29.1),
    ("search newest", "conversation"): (29.0, 29.0, 29.2),
}


def declared(pooled: bool = False) -> dict[Case, float]:
    return {
        Case(operation, series, size if series == "tiny" else size * MIB): (
            POOLED[budget] if pooled else budget
        )
        for (operation, series), (sizes, budget) in DECLARED.items()
        for size in sizes
    }


def measured(
    peaks: dict[Case, float], warm: bool = True, samples: int = 5
) -> list[Measured]:
    """Five fresh processes per case at steady state unless told otherwise,
    all at `peak`."""
    return [
        Measured(case, peak, 0.1, 1, 1, warm, sample)
        for case, peak in peaks.items()
        for sample in range(samples)
    ]


def recorded() -> dict[Case, float]:
    """The recorded warm peaks, the other cases flat and well within."""
    peaks = within()
    for (operation, series), values in RECORDED.items():
        sizes = DECLARED[(operation, series)][0]
        for size, peak in zip(sizes, values, strict=True):
            peaks[Case(operation, series, size * MIB)] = peak
    return peaks


def within() -> dict[Case, float]:
    """A peak for every case, flat and under budget."""
    return {case: budget - 4.0 for case, budget in declared().items()}


def test_the_matrix_has_every_operation_series_and_size() -> None:
    assert len(bench.matrix()) == len(set(bench.matrix())) == 49
    assert set(bench.matrix()) == set(declared())


def test_both_backends_run_in_make_and_ci() -> None:
    makefile = (ROOT / "Makefile").read_text()
    recipe = makefile.split("\nbench-memory:", 1)[1].split("\n\n", 1)[0]
    assert "scripts/bench_records_memory.py --backends $(or $(backends),file)" in recipe
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    memory = ci.split("\n  memory:\n", 1)[1].split("\n\n", 1)[0]
    assert "run: make bench-memory backends=file,postgres" in memory


def test_the_budgets_are_the_declared_formula() -> None:
    assert (bench.BASE, bench.PART, bench.RECORD, bench.SPOOL) == (
        10.5,
        983_040,
        81_920,
        MIB,
    )
    assert (bench.FORMAT_ONE, bench.CANDIDATE, bench.HIT, bench.SWEPT) == (
        0,
        64,
        16_384,
        2_048,
    )
    assert (bench.STEP, bench.FLAT, bench.RATIO, bench.LIMIT) == (8.0, 4.0, 2.0, 20)
    assert (bench.FLAT_TARGET, bench.FILE_GUARD, bench.POSTGRES_GUARD) == (2.0, 32, 48)
    assert bench.SAMPLES == 5
    assert {case: bench.budget(case) for case in bench.matrix()} == declared()
    assert (bench.POOL_CONNECTIONS, bench.POOL_BUFFER) == (4, 4 * MIB)
    assert (bench.LEVEL, bench.LEVEL_PARTS, bench.WARM_LIMIT) == (0.25, 8, 64)
    pooled = {case: bench.budget(case, 4) for case in bench.matrix()}
    assert pooled == declared(pooled=True)


def test_the_pool_size_is_read_from_the_store_code() -> None:
    source = (ROOT / "crates/neosian/src/postgres/connection.rs").read_text()
    assert re.findall(r"const CONNECTIONS: usize = (\d+);", source) == ["4"]
    bench.require_pool(source)
    for changed in (source.replace("usize = 4;", "usize = 6;"), ""):
        with pytest.raises(GateFailure, match="redeclare POOL_CONNECTIONS"):
            bench.require_pool(changed)


def test_a_process_measured_without_the_warm_up_fails_on_both_stores() -> None:
    assert bench.verdicts(measured(recorded()), pool=4) == []
    for pool in (0, 4):
        cold = bench.verdicts(measured(within(), warm=False), pool=pool)
        assert len(cold) == 49
        assert all(f.endswith(": no steady-state warm-up") for f in cold)
    one = measured(recorded())
    for m in one:
        m.warm = not (m.case == Case("seal", "result", MIB) and m.sample == 2)
    assert bench.verdicts(one, pool=4) == ["seal result 1 MiB: no steady-state warm-up"]


def test_the_first_linux_figures_pass_the_guards_and_miss_the_targets() -> None:
    """CI run 46069550958 on ubuntu: FileStore restore parts at 24.1 MiB,
    rising 2.42; PostgreSQL seals at 33.8 with a peak of 35.6 (K7)."""
    restore = [Case("restore part", "messages", n * MIB) for n in TURNS]
    results = measured(within())
    for case, median in zip(restore, (21.7, 23.0, 24.12), strict=True):
        spread(case, [median] * 5, results)
    assert bench.verdicts(results) == []
    assert bench.met(24.12 - 21.7, bench.FLAT_TARGET) == "not met"
    assert bench.met(24.12, bench.declared(restore[-1])) == "not met"
    seal = Case("seal", "result", 16 * MIB)
    pooled = measured(recorded())
    spread(seal, [33.8, 33.8, 35.6, 33.0, 34.0], pooled)
    spread(Case("seal", "result", MIB), [33.0] * 5, pooled)
    spread(Case("seal", "result", 4 * MIB), [33.2] * 5, pooled)
    assert bench.verdicts(pooled, pool=4) == []
    assert bench.met(33.8, bench.declared(seal, 4)) == "not met"


# FileStore steady medians recorded under K4 on 2026-11-28 (MiB).
STEADY_FILES = {
    ("seal", "result"): 18.1,
    ("piece fetch", "result"): 18.5,
    ("restore part", "result"): 19.4,
    ("read full", "conversation"): 18.5,
    ("read light", "conversation"): 18.7,
    ("search relevance", "conversation"): 18.5,
    ("sweep preview", "result"): 18.0,
}


def test_one_guard_per_store_and_every_target_printed() -> None:
    assert (bench.ceiling(), bench.ceiling(4)) == (32.0, 48.0)
    assert bench.FILE_CEILING == 24.0
    assert {case: bench.declared(case) for case in bench.matrix()} == dict.fromkeys(
        bench.matrix(), 24.0
    )
    assert {case: bench.declared(case, 4) for case in bench.matrix()} == declared(
        pooled=True
    )
    peaks = within()
    for case in peaks:
        peaks[case] = STEADY_FILES.get((case.operation, case.series), peaks[case])
    assert bench.verdicts(measured(peaks)) == []
    seal = Measured(Case("seal", "result", MIB), 18.1, 0.1, 1, 1, True)
    part = Measured(Case("write part", "result", MIB), 24.5, 0.1, 1, 1, True)
    row = bench._row("file/steady", seal, 32.0, "ok", 0)
    assert "macOS-declared 24 met, idle-process budget (item 16.1) 16 not met" in row
    row = bench._row("file/steady", part, 32.0, "ok", 0)
    assert (
        "macOS-declared 24 not met, idle-process budget (item 16.1) 24 not met" in row
    )
    seal.peak = 33.0
    row = bench._row("postgres/steady", seal, 48.0, "ok", 4)
    assert row.startswith("postgres/steady seal")
    assert (
        "macOS-declared 32 not met, idle-process budget (item 16.1) 16 not met" in row
    )


def test_the_steady_state_rule_is_the_declared_one() -> None:
    """Level once the resident size rose at most 256 KiB over eight parts."""
    rising = [12.1, 26.1, 27.0, 27.6, 28.0, 28.1, 28.2, 28.3, 28.3, 28.3]
    assert bench.level_after(rising + [28.3] * 8) == 13  # 28.3 over 28.1
    assert bench.level_after([12.0] + [17.9] * 9) == 9  # nine readings level
    assert bench.level_after([12.0] + [17.9] * 8) is None
    assert bench.level_after([10.0 + 0.04 * n for n in range(65)]) is None
    assert bench.level_after([10.0 + 0.03 * n for n in range(65)]) == 8
    step = [12.0] + [17.9] * 5 + [18.2] * 4  # 0.3 MiB within the last eight
    assert bench.level_after(step) is None


def fake_server() -> tuple[Any, list[tuple[str, dict[str, Any]]]]:
    sent: list[tuple[str, dict[str, Any]]] = []

    def post(route: str, body: dict[str, Any]) -> dict[str, Any]:
        sent.append((route, body))
        return {"state": "discarded" if body.get("discard") else "open"}

    return post, sent


def test_the_warm_up_sends_full_parts_until_level_then_discards() -> None:
    bodies = bench.warm_bodies()
    sizes = [len(json.dumps(body, separators=(",", ":"))) for body in bodies]
    assert len(bodies) == 64 and all(900_000 < size <= 983_040 for size in sizes)
    post, sent = fake_server()
    readings = iter([12.0, 20.0, 24.0, 26.0] + [27.0] * 100)
    assert bench.warm_up(post, lambda: next(readings)) == (12, True)  # 27.0 x 9
    assert len(sent) == 13 and len({body["draft"] for _, body in sent}) == 1
    assert sent[-1][1]["discard"] is True and sent[-1][1]["records"] == []
    assert [body["records"] for _, body in sent[:12]] == [
        body["records"] for body in bodies[:12]
    ]


def test_a_process_that_never_levels_fails_and_names_its_operation() -> None:
    post, sent = fake_server()
    growth = iter([12.0 + 0.5 * n for n in range(100)])
    assert bench.warm_up(post, lambda: next(growth)) == (64, False)
    assert len(sent) == 65 and sent[-1][1]["discard"] is True
    results = measured(within())
    for m in results:
        if m.case == Case("restore part", "messages", 16 * MIB) and m.sample == 3:
            m.levelled, m.warm_parts = False, 64
    assert bench.verdicts(results) == [
        "restore part messages 16 MiB #3: resident size never levelled within 64 parts"
    ]


def test_a_flat_matrix_within_budget_passes() -> None:
    assert bench.verdicts(measured(within())) == []


def test_a_rise_past_four_mib_fails_and_four_passes() -> None:
    peaks = within()
    largest = Case("write part", "result", 16 * MIB)
    smallest = Case("write part", "result", MIB)
    peaks[largest] = peaks[smallest] + 4.0
    assert bench.verdicts(measured(peaks)) == []
    peaks[largest] = peaks[smallest] + 4.01
    assert bench.verdicts(measured(peaks)) == [
        "write part result: not flat, rose 4.01 MiB"
    ]
    assert bench.flatness({1: 12.0, 4: 20.0, 16: 13.0}) == (1.0, True)


def test_a_median_over_the_store_ceiling_fails() -> None:
    peaks = dict.fromkeys(declared(), 31.5)
    peaks[Case("seal", "messages", 4 * MIB)] = 32.1
    assert bench.verdicts(measured(peaks)) == [
        "seal messages 4 MiB: median peak 32.1 MiB over its ceiling of 32"
    ]
    pooled = dict.fromkeys(declared(), 47.5)
    pooled[Case("seal", "messages", 4 * MIB)] = 48.1
    assert bench.verdicts(measured(pooled), pool=4) == [
        "seal messages 4 MiB: median peak 48.1 MiB over its ceiling of 48"
    ]


def test_a_case_left_out_fails() -> None:
    peaks = within()
    del peaks[Case("sweep preview", "result", 16 * MIB)]
    assert bench.verdicts(measured(peaks)) == [
        "not measured: sweep preview result 16 MiB"
    ]


def test_the_seal_ratio_compares_the_16_mib_median_with_the_1_mib_median() -> None:
    fast, slow = [1.0, 1.1, 0.9, 5.0, 1.0], [2.0, 2.1, 1.9, 0.1, 2.0]
    assert bench.ratio({MIB: fast, 4 * MIB: [9.0], 16 * MIB: slow}) == (2.0, True)
    value, ok = bench.ratio({MIB: fast, 4 * MIB: [1.0], 16 * MIB: [2.2, 2.1, 2.3]})
    assert (round(value, 2), ok) == (2.2, False)
    value, ok = bench.ratio({MIB: [3.0, 3.0], 4 * MIB: [1.0], 16 * MIB: [1.0, 1.0]})
    assert (round(value, 2), ok) == (0.33, True)


@pytest.mark.parametrize(
    "capabilities",
    [{}, {"wire_version": 6}, {"turn_records": {"version": 2}}, {"turn_records": 1}],
)
def test_a_store_without_records_version_1_fails_rather_than_skips(
    capabilities: dict[str, Any],
) -> None:
    with pytest.raises(GateFailure, match="no turn_records version 1"):
        bench.require_records(capabilities)
    bench.require_records({"wire_version": 6, "turn_records": {"version": 1}})


def page(records: int, turns: list[int], more: str | None) -> dict[str, Any]:
    items = [{"text": "abcd"} for _ in range(records)]
    return {
        "turns": [{"turn": n} for n in turns],
        "records": items,
        "continuation": more,
    }


def test_a_complete_read_back_passes() -> None:
    pages = [page(2, [1], "next"), page(1, [1, 2], None)]
    assert bench.check_pages(pages, turns=2, count=3, text=12) == 3


@pytest.mark.parametrize(
    "pages",
    [
        [page(2, [1], "next"), page(0, [], None)],  # an empty page
        [page(2, [1], "next")],  # stopped before the last page
        [page(2, [1], None)],  # a record missing
        [page(3, [1], None)],  # a turn missing
        [],
    ],
)
def test_an_incomplete_read_back_fails(pages: list[dict[str, Any]]) -> None:
    with pytest.raises(GateFailure):
        bench.check_pages(pages, turns=2, count=3, text=12)


def test_search_hits_are_read_back() -> None:
    hit = {"conversation_id": "r-conversation-16", "score": 1}
    full = {"hits": [hit] * 20, "order": "relevance"}
    assert bench.check_hits(full, "r-conversation-16", "relevance") == 20
    for answer in (
        {"hits": [hit] * 19, "order": "relevance"},
        {"hits": [{**hit, "conversation_id": "other"}] * 20, "order": "relevance"},
        {"hits": [hit] * 20},
        {"hits": [{"conversation_id": "r-conversation-16"}] * 20, "order": "relevance"},
    ):
        with pytest.raises(GateFailure):
            bench.check_hits(answer, "r-conversation-16", "relevance")


def test_an_answer_that_differs_fails() -> None:
    bench.expect({"state": "open", "next_seq": 3}, state="open")
    with pytest.raises(GateFailure):
        bench.expect({"state": "open", "next_seq": 3}, state="open", next_seq=4)


@pytest.mark.parametrize("shape", ["result", "messages"])
def test_the_corpus_is_valid_on_the_wire(shape: str) -> None:
    """Pieces add up to the size, every part is within the binary's 983,040
    bytes, every chunk within 65,536 encoded bytes, a term in every record."""
    for size in (MIB, 16 * MIB):
        turn = bench.pieces(shape, size)
        assert sum(piece[2] for piece in turn) == size
        items = bench.records(turn)
        assert [item["seq"] for item in items] == list(range(len(items)))
        assert len(items) == bench.record_count([turn])
        assert all(len(json.dumps(item["text"])) <= 65_536 for item in items)
        assert all(re.search(r"\bzq\b", item["text"]) for item in items)
        bodies = bench.parts({"conversation_id": "c", "draft": "d"}, items, {"x": 1})
        assert [item for body in bodies for item in body["records"]] == items
        sizes = [len(json.dumps(body, separators=(",", ":"))) for body in bodies]
        assert max(sizes) <= 983_040 and "x" in bodies[0] and "x" not in bodies[-1]
    assert [piece[2] for piece in bench.pieces("messages", 4 * MIB)] == [
        1_258_291,
        1_258_291,
        1_258_291,
        419_431,
    ]


def test_five_fresh_processes_per_case_are_required() -> None:
    failures = bench.verdicts(measured(within(), samples=4))
    assert len(failures) == 49 and all(f.endswith("4 samples, not 5") for f in failures)
    one = measured(within())
    del one[-1]
    assert bench.verdicts(one) == ["sweep preview result 16 MiB: 4 samples, not 5"]


def spread(case: Case, values: list[float], results: list[Measured]) -> None:
    for m in results:
        if m.case == case:
            m.peak = values[m.sample]


def test_flatness_and_a_budget_compare_medians() -> None:
    small = Case("restore part", "messages", MIB)
    large = Case("restore part", "messages", 16 * MIB)
    results = measured(recorded(), warm=True)
    spread(small, [27.6, 29.0, 28.4, 28.9, 28.1], results)
    spread(large, [30.1, 30.2, 28.0, 29.9, 27.9], results)
    assert bench.series_peaks(results)[("restore part", "messages")][16 * MIB] == 29.9
    assert bench.verdicts(results, pool=4) == []  # medians 28.4 and 29.9
    spread(large, [32.5, 32.5, 32.5, 29.9, 32.5], results)
    assert bench.verdicts(results, pool=4) == [
        "restore part messages: not flat, rose 4.10 MiB"
    ]
    reads = [Case("read full", "conversation", n * MIB) for n in CONVERSATIONS]
    results = measured(within())
    for read in reads:
        spread(read, [30.6, 30.5, 30.6, 30.6, 32.1], results)
    assert bench.verdicts(results) == []  # one sample over 32, the median 30.6
    for read in reads:
        spread(read, [32.1, 30.5, 32.2, 30.6, 32.1], results)
    assert bench.verdicts(results) == [
        f"read full conversation {n} MiB: median peak 32.1 MiB over its ceiling of 32"
        for n in CONVERSATIONS
    ]


def test_a_sample_more_than_two_mib_over_its_budget_fails() -> None:
    assert bench.OUTLIER == 2.0
    results = measured(within())
    for n in CONVERSATIONS:
        spread(
            Case("read full", "conversation", n * MIB),
            [30.6, 30.5, 34.0, 30.6, 30.5],
            results,
        )
    assert bench.verdicts(results) == []
    read = Case("read full", "conversation", 64 * MIB)
    spread(read, [30.6, 30.5, 34.01, 30.6, 30.5], results)
    assert bench.verdicts(results) == [
        "read full conversation 64 MiB: a peak of 34.0 MiB passes its ceiling of 32"
        " by more than 2 MiB"
    ]
