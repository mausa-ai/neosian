"""llms.txt x2 — the outer door, byte-pinned (DESIGN §14.4, ledger #79).

The repo-root copy is what an agent finds at the git URL; the packaged
copy is what travels in the wheel. One test keeps them byte-identical
(no hatch force-include), one keeps the install pin version-true.
"""

import re
from importlib import resources
from pathlib import Path

import neosian

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _repo_copy() -> bytes:
    return (_REPO_ROOT / "llms.txt").read_bytes()


def _packaged_copy() -> bytes:
    return resources.files("neosian.assets").joinpath("llms.txt").read_bytes()


def test_the_two_copies_are_byte_identical() -> None:
    assert _repo_copy() == _packaged_copy()


def test_the_shipped_copy_reaches_the_package() -> None:
    text = _packaged_copy().decode("utf-8")
    assert text.startswith("# neosian\n\n> ")


def test_it_opens_the_docs_door() -> None:
    text = _packaged_copy().decode("utf-8")
    assert "neosian docs" in text
    assert "neosian memory" in text
    assert "neosian mcp install" in text


# Ids that left the enum (CHANGELOG, Removed): a shipped page naming one is
# stale. `baselines.md` is history and keeps them. An id is matched whole:
# `claude-opus-5` inside `claude-opus-5-5` is a different row.
_RETIRED = (
    "gpt-5-mini-2025-08-07",
    "gpt-5-nano-2025-08-07",
    "gpt-5-pro-2025-10-06",
    "gemma-4-31b",
    "claude-opus-4-6",
    "claude-haiku-4-5",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "claude-opus-5",
)


def _names(text: str, retired: str) -> bool:
    return re.search(rf"(?<![\w.-]){re.escape(retired)}(?![\w.-])", text) is not None


def test_no_shipped_page_names_a_retired_id() -> None:
    pages = [
        p
        for p in (_REPO_ROOT / "neosian" / "assets" / "docs").glob("*.md")
        if p.name != "baselines.md"
    ]
    pages.append(_REPO_ROOT / "README.md")
    texts = {p.name: p.read_text(encoding="utf-8") for p in pages}
    texts["llms.txt"] = _packaged_copy().decode("utf-8")
    for name, text in texts.items():
        for retired in _RETIRED:
            assert not _names(text, retired), f"{name} names the retired id {retired!r}"
    assert _names("`claude-opus-5`", "claude-opus-5")
    assert not _names("`claude-opus-5-5`", "claude-opus-5")


def test_the_install_pin_matches_the_version() -> None:
    # Deliberate coupling: a release bumps llms.txt in the same commit,
    # or this goes red — the stale-README-pin failure mode, closed.
    text = _packaged_copy().decode("utf-8")
    assert f'"neosian=={neosian.__version__}"' in text
    assert f"ghcr.io/mausa-ai/neosian:{neosian.__version__}" in text
