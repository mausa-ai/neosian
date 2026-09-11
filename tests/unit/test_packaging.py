"""The dependency list 1.0 would lock (NF slice C, DESIGN §27.11; NX §29.10).

One package carries every door but one (ledger #206) — the shell, the MCP
SDK, the serving stack and the OTel API are core; the Postgres driver is
the one extra with `all` as its alias, and the four former extras are
empty aliases for one release; the three provider SDKs
are capped at their next major (TP-1); `import neosian` loads no provider
SDK (EC-5); and a missing library still answers with a reinstall hint,
never a traceback — each pinned here, keylessly, in a subprocess where
the question is what an import loads.
"""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

_PYPROJECT = Path(__file__).parents[2] / "pyproject.toml"
_DOORS = {
    "mcp",
    "opentelemetry-api",
    "rich",
    "starlette",
    "tomli-w",
    "typer",
    "uvicorn",
}
_ALIASES = {"cli", "mcp", "otel", "server"}
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
def test_the_core_carries_every_door_but_the_driver() -> None:
    project = _project()
    core = project["dependencies"]
    assert isinstance(core, list)
    names = {_name(s) for s in core}
    assert names >= _DOORS and "psycopg" not in names
    extras = project["optional-dependencies"]
    assert isinstance(extras, dict)
    assert set(extras) == _ALIASES | {"postgres", "all"}
    assert [_name(s) for s in extras["postgres"]] == ["psycopg"]
    assert extras["all"] == ["neosian[postgres]"]
    assert all(extras[name] == [] for name in _ALIASES), extras


@pytest.mark.unit
def test_the_sdk_floors_are_capped() -> None:
    core = _project()["dependencies"]
    assert isinstance(core, list)
    specs = {_name(s): s for s in core}
    for sdk in _SDKS:
        assert ">=" in specs[sdk] and ",<" in specs[sdk], specs[sdk]


@pytest.mark.unit
def test_the_console_script_hints_without_typer() -> None:
    result = _run(
        "import sys; sys.modules['typer'] = None; "
        "from neosian._cli.entry import main; main()"
    )
    assert result.returncode == 1
    assert "install neosian" in result.stderr
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
        "    assert 'install neosian' in str(exc), exc\n"
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
