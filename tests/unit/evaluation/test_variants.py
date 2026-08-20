"""Variant prompt files — strict keys, the eval_* codes fire (DESIGN §13)."""

import textwrap
from pathlib import Path

import pytest

from neosian._foundation.evaluation.variants import load_variant
from neosian._foundation.shared.exceptions import (
    EvalConfigInvalidYAMLError,
    EvalConfigMissingKeyError,
    EvalConfigUnknownKeyError,
    EvalPromptNotFoundError,
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "variant.yaml"
    path.write_text(textwrap.dedent(body))
    return path


@pytest.mark.unit
class TestLoadVariant:
    def test_full_variant(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            """
            system_prompt: |
              You are minimal.
            tools:
              draw: {description: "Draw."}
              speak: {description: "Speak."}
            """,
        )
        variant = load_variant("minimal", path)
        assert variant.name == "minimal"
        assert variant.system_prompt == "You are minimal.\n"
        assert variant.tool_descriptions == {"draw": "Draw.", "speak": "Speak."}
        assert variant.source == str(path)

    def test_missing_file_is_eval_prompt_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(EvalPromptNotFoundError):
            load_variant("v", tmp_path / "absent.yaml")

    def test_missing_system_prompt(self, tmp_path: Path) -> None:
        with pytest.raises(EvalConfigMissingKeyError):
            load_variant("v", _write(tmp_path, "tools: {}\n"))

    @pytest.mark.parametrize(
        "body",
        [
            "system_prompt: [not\n",  # unparseable
            "- a list\n",
            "system_prompt: 3\n",
            "system_prompt: x\ntools: nope\n",
            "system_prompt: x\ntools:\n  draw: {}\n",
            "system_prompt: x\ntools:\n  draw: {description: 3}\n",
        ],
    )
    def test_malformed_files(self, tmp_path: Path, body: str) -> None:
        with pytest.raises(EvalConfigInvalidYAMLError):
            load_variant("v", _write(tmp_path, body))

    @pytest.mark.parametrize(
        ("body", "dropped"),
        [
            ("system_prompt: x\ncompact_summarize: y\n", True),
            ("system_prompt: x\non_success: y\n", True),
            ("system_prompt: x\nnotes: y\n", False),
            (
                "system_prompt: x\ntools:\n"
                "  draw: {description: d, on_success: hint}\n",
                True,
            ),
        ],
    )
    def test_unknown_keys_fail(self, tmp_path: Path, body: str, dropped: bool) -> None:
        with pytest.raises(EvalConfigUnknownKeyError) as excinfo:
            load_variant("v", _write(tmp_path, body))
        assert ("never read" in str(excinfo.value)) is dropped
