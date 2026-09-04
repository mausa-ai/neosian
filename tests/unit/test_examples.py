"""The examples are part of the surface: every one imports keylessly and
none reaches past the pinned API (NF — TP-9, EC-4)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES = sorted(p for p in (_ROOT / "examples").glob("*.py"))
_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CEREBRAS_API_KEY")


@pytest.mark.unit
@pytest.mark.parametrize("example", _EXAMPLES, ids=lambda p: p.stem)
def test_example_imports_keylessly(example: Path) -> None:
    """Importing an example runs its module body only (every `main` sits
    behind `__main__`), with no provider key in the environment."""
    env = {k: v for k, v in os.environ.items() if k not in _KEY_VARS}
    subprocess.run(
        [sys.executable, "-c", f"import examples.{example.stem}"],
        check=True,
        cwd=_ROOT,
        env=env,
    )


@pytest.mark.unit
def test_examples_import_only_the_public_api() -> None:
    """`neosian._foundation` never appears in an example (EC-4) — the
    most-read code must not teach users past `__all__`."""
    offenders = [
        p.name for p in _EXAMPLES if "neosian._foundation" in p.read_text("utf-8")
    ]
    assert offenders == []
