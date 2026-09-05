"""The dependency list 1.0 would lock (NF slice C, DESIGN §27.9).

The shell rides the `cli` extra (TP-2), the three provider SDKs are
capped at their next major (TP-1), and `import neosian` loads no provider
SDK (EC-5) — each pinned here, keylessly, in a subprocess where the
question is what an import loads.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

_PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"
_CLI_EXTRA = {"rich", "simple-term-menu", "tomli-w", "typer"}
_SDKS = ("anthropic", "cerebras-cloud-sdk", "openai")


def _project() -> dict[str, object]:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]  # type: ignore[no-any-return]


def _name(spec: str) -> str:
    return spec.split(">")[0].split("[")[0]


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )


@pytest.mark.unit
def test_the_shell_rides_the_cli_extra() -> None:
    project = _project()
    extras = project["optional-dependencies"]
    assert isinstance(extras, dict)
    assert {_name(s) for s in extras["cli"]} == _CLI_EXTRA
    core = project["dependencies"]
    assert isinstance(core, list)
    assert not _CLI_EXTRA & {_name(s) for s in core}


@pytest.mark.unit
def test_the_sdk_floors_are_capped() -> None:
    core = _project()["dependencies"]
    assert isinstance(core, list)
    specs = {_name(s): s for s in core}
    for sdk in _SDKS:
        assert ">=" in specs[sdk] and ",<" in specs[sdk], specs[sdk]


@pytest.mark.unit
def test_the_console_script_hints_without_the_extra() -> None:
    result = _run(
        "import sys; sys.modules['typer'] = None; "
        "from neosian._cli.entry import main; main()"
    )
    assert result.returncode == 1
    assert "neosian[cli]" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.unit
def test_the_eval_facade_imports_without_rich_and_hints_at_use() -> None:
    result = _run(
        "import sys\n"
        "sys.modules['rich'] = None\n"
        "import neosian.evaluation as ev\n"
        "from neosian._foundation.evaluation.results import EvalReport\n"
        "report = EvalReport(suite='s', variants=(), models=(), cases=(), results=())\n"
        "try:\n"
        "    ev.print_report(report, None)\n"
        "except ImportError as exc:\n"
        "    assert 'neosian[cli]' in str(exc), exc\n"
        "else:\n"
        "    raise SystemExit('no hint')"
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.unit
def test_import_neosian_loads_no_provider_sdk() -> None:
    result = _run(
        "import sys, neosian\n"
        "from neosian import Agent, AgentConfig, Model\n"
        "Agent(AgentConfig(model=Model.FAKE, system_prompt='x'))\n"
        "loaded = {'anthropic', 'openai', 'cerebras'} & set(sys.modules)\n"
        "assert not loaded, loaded\n"
        "clients = [m for m in sys.modules if m.startswith("
        "'neosian._foundation.llm.') and m.rsplit('.', 1)[1] in "
        "('anthropic', 'openai', 'cerebras')]\n"
        "assert not clients, clients"
    )
    assert result.returncode == 0, result.stderr
