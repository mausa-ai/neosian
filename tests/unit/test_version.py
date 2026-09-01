"""Pins the version derivation: one literal in pyproject, everything follows.

"Everything" includes the release surface: README's git-URL install pins
(they went stale at v0.70.0 — found at NA — and at v0.78.0 — found at NM;
``test_llms_txt.py`` gates only llms.txt) and the declaration's two
otherwise-ungated flips — the ``Development Status`` classifier and README's
Stability tense — which a 1.0 bump must carry in the same commit.
"""

import importlib.metadata
import re
import tomllib
from pathlib import Path
from typing import Any

import neosian

PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"
_README = PYPROJECT.parent / "README.md"
_PIN = re.compile(r"github\.com/neosae/neosian@(v\d+\.\d+\.\d+)")
_FINAL = re.compile(r"\d+\.\d+\.\d+")
_UNDECLARED = "deliberately not yet cut"
_STABLE = "Development Status :: 5 - Production/Stable"
_BETA = "Development Status :: 4 - Beta"


def _pyproject() -> dict[str, Any]:
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def test_version_derives_from_installed_metadata() -> None:
    assert neosian.__version__ == importlib.metadata.version("neosian")


def test_pyproject_is_the_single_source() -> None:
    pyproject = _pyproject()
    assert neosian.__version__ == pyproject["project"]["version"]
    assert "version" not in pyproject["project"].get("dynamic", [])


def test_every_readme_install_pin_is_the_current_release() -> None:
    pins = _PIN.findall(_README.read_text(encoding="utf-8"))
    assert pins, "README carries no git-URL install pin"
    assert set(pins) == {f"v{neosian.__version__}"}


def test_the_declaration_is_all_or_nothing() -> None:
    # A final 1.x release flips the classifier and README's Stability tense
    # in the same commit, or this goes red — neither flip is gated otherwise.
    # A pre-release (1.0.0rc1) is the kit's test vehicle, not the declaration.
    version = neosian.__version__
    declared = bool(_FINAL.fullmatch(version)) and int(version.split(".")[0]) >= 1
    classifiers = _pyproject()["project"]["classifiers"]
    assert (_STABLE in classifiers) == declared
    assert (_BETA in classifiers) == (not declared)
    readme = _README.read_text(encoding="utf-8")
    start = readme.index("## Stability")
    end = readme.find("\n## ", start + 1)
    stability = readme[start : end if end != -1 else len(readme)]
    assert (_UNDECLARED in stability) == (not declared)
