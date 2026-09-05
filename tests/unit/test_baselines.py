"""The baselines page is fingerprint-gated (ROADMAP NV, DESIGN §13.12).

The published numbers are only meaningful against the exact prompt pack
and scenario pack they measured. This gate makes a silent edit to either
file fail `make test` until BASELINES.md records a re-run — the
PRICES_FINGERPRINT idiom applied to the benchmark itself.
"""

import hashlib
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASELINES = _REPO_ROOT / "neosian" / "assets" / "docs" / "baselines.md"

# Every file the baselines' meaning depends on. Adding a gated file
# means adding its fingerprint line to BASELINES.md's Fingerprints
# section in the same change.
_GATED = (
    "neosian/assets/prompts/memory.yaml",
    "neosian/assets/prompts/reflection.yaml",
    "neosian/assets/prompts/maintenance.yaml",
    "examples/eval_memory_baseline.yaml",
)

_FINGERPRINT = re.compile(r"`([^`]+\.yaml)` — sha256\s*\n?\s*`([0-9a-f]{64})`")


def _recorded_fingerprints() -> dict[str, str]:
    text = _BASELINES.read_text(encoding="utf-8")
    return dict(_FINGERPRINT.findall(text))


@pytest.mark.unit
def test_baselines_file_exists() -> None:
    assert _BASELINES.is_file(), "BASELINES.md is the published-numbers home (NV)"


@pytest.mark.unit
@pytest.mark.parametrize("relative", _GATED)
def test_gated_file_matches_the_recorded_fingerprint(relative: str) -> None:
    recorded = _recorded_fingerprints()
    assert relative in recorded, (
        f"BASELINES.md records no fingerprint for {relative} — the "
        "Fingerprints section must name every gated file"
    )
    actual = hashlib.sha256((_REPO_ROOT / relative).read_bytes()).hexdigest()
    assert actual == recorded[relative], (
        f"{relative} changed without a recorded baseline re-run — re-run "
        "the external baselines and update BASELINES.md (fingerprint + "
        "results) in the same change"
    )


@pytest.mark.unit
def test_results_name_every_measured_provider() -> None:
    text = _BASELINES.read_text(encoding="utf-8")
    results = text[text.index("## Results") :]
    for provider in ("Anthropic", "OpenAI", "Cerebras", "xAI", "Gemini"):
        assert provider in results, f"Results tables must name {provider}"
