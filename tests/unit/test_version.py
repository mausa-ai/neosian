"""Pins the version derivation: one literal in pyproject, everything follows.

"Everything" includes the release surface: README's install pins (they
went stale at v0.70.0 — found at NA — and at v0.78.0 — found at NM;
``test_llms_txt.py`` gates only llms.txt), the README's and llms.txt's
links — absolute at the release tag, because PyPI renders the README
without rewriting them (NX) and the wheel carries llms.txt (TP-32) — the reader-facing files never calling the repository private again
(TP-5 inverted at NX), and the declaration's two otherwise-ungated flips —
the ``Development Status`` classifier and README's Stability tense — which
a 1.0 bump must carry in the same commit.
"""

import importlib.metadata
import re
import tomllib
from pathlib import Path
from typing import Any

import neosian

PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"
_ROOT = PYPROJECT.parent
_README = _ROOT / "README.md"
_READER_FACING = (
    _README,
    _ROOT / "llms.txt",
    _ROOT / "neosian" / "assets" / "llms.txt",
    _ROOT / "neosian" / "assets" / "docs" / "quickstart.md",
)
_PIN = re.compile(r'"neosian(?:\[[a-z,]+\])?==([^"]+)"')
_TAG_REF = re.compile(
    r"(?:github\.com/mausa-ai/neosian/(?:blob|tree)|"
    r"raw\.githubusercontent\.com/mausa-ai/neosian)/(v[^/]+)/"
)
_RELATIVE = re.compile(r"\]\((?!https?://|#)")
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
    assert pins, "README carries no install pin"
    assert set(pins) == {neosian.__version__}


def test_every_readme_link_is_absolute_at_the_release_tag() -> None:
    # PyPI renders the README as is: a relative link dangles there, and a
    # link at `master` drifts from the release it describes.
    readme = _README.read_text(encoding="utf-8")
    refs = _TAG_REF.findall(readme)
    assert refs, "README carries no link at a release tag"
    assert set(refs) == {f"v{neosian.__version__}"}
    assert not _RELATIVE.search(readme), "README carries a relative link"
    assert 'src="https://' in readme and 'src="branding' not in readme


def test_every_llms_txt_link_is_absolute_at_the_release_tag() -> None:
    # The wheel carries llms.txt where no repository path resolves (TP-32);
    # `test_llms_txt.py` keeps the two copies byte-identical.
    text = (_ROOT / "llms.txt").read_text(encoding="utf-8")
    refs = _TAG_REF.findall(text)
    assert refs, "llms.txt carries no link at a release tag"
    assert set(refs) == {f"v{neosian.__version__}"}
    assert not _RELATIVE.search(text), "llms.txt carries a relative link"


def test_nothing_reader_facing_calls_the_repository_private() -> None:
    # The premise of the git-URL install form, closed at NX (TP-5 inverted):
    # the repository is public and the package is on PyPI.
    for path in _READER_FACING:
        text = path.read_text(encoding="utf-8")
        assert "repository is private" not in text, path.name
        assert "private repository" not in text, path.name
        assert "git+ssh://" not in text, path.name


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
