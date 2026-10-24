"""The major-version boundary and embedded manifests are explicit contracts."""

import json

import pytest

from tests.contract.support import ROOT, Binary, package_version


def test_docs_and_schemas_are_the_shipped_assets(binary: Binary) -> None:
    topics = (ROOT / "assets/docs/topics.txt").read_text().splitlines()
    listed = binary.json("docs")
    assert [page["topic"] for page in listed["topics"]] == topics
    for topic in topics:
        page = binary.json("docs", topic)
        source = (ROOT / "assets/docs" / f"{topic}.md").read_text()
        assert page["body"] == source.split("---\n", 2)[2].strip()
    for kind, name in [
        ("state", "state-v1.schema.json"),
        ("wire", "wire-v6.openapi.json"),
        ("errors", "error-codes.json"),
        ("profile", "profile-v1.schema.json"),
        ("grants", "grants-v1.schema.json"),
        ("knowledge", "knowledge-v1.schema.json"),
        ("skill", "skill-v1.schema.json"),
    ]:
        printed = binary.run("schema", kind)
        assert printed.returncode == 0
        assert json.loads(printed.stdout) == json.loads(
            (ROOT / "assets/contracts" / name).read_text()
        )
    assert binary.run("version").stdout == f"neosian v{package_version()}\n"


@pytest.mark.parametrize(
    "args",
    [
        ("chat",),
        ("playground",),
        ("eval",),
        ("reflect",),
        ("configure",),
        ("memory", "maintain", "--model", "fake"),
    ],
)
def test_retired_commands_refuse_without_building_a_home(
    binary: Binary, args: tuple[str, ...]
) -> None:
    result = binary.run(*args)
    assert result.returncode == 2
    assert not binary.home.exists()
    assert "uv tool install" not in result.stderr


def test_status_leaves_legacy_credentials_untouched(binary: Binary) -> None:
    binary.home.mkdir()
    config = binary.home / "config.toml"
    data = b'[credentials]\nopenai_api_key = "synthetic-private-value"\n[update]\nmode = "off"\n'
    config.write_bytes(data)
    result = binary.run("status", "--json")
    assert result.returncode == 0
    assert "providers" not in json.loads(result.stdout)
    assert "synthetic-private-value" not in result.stdout + result.stderr
    assert config.read_bytes() == data


@pytest.mark.parametrize(
    "name", ["bad-name", "x;drop schema public", "Upper", "x" * 64]
)
def test_invalid_schema_name_builds_nothing(binary: Binary, name: str) -> None:
    result = binary.run("schema", "postgres", "--schema", name)
    assert result.returncode == 2 and not binary.home.exists()


def test_error_identifiers_survive_the_major_cut(binary: Binary) -> None:
    old = json.loads(
        (ROOT / "tests/contract/fixtures/v1/error-codes.json").read_text()
    )["codes"]
    inventory = binary.run("schema", "errors")
    entries = json.loads(inventory.stdout)["codes"]
    by_code = {entry["code"]: entry for entry in entries}
    assert len(by_code) == len(entries)
    assert set(old) <= set(by_code)
    assert all(entry["status"] in {"active", "reserved"} for entry in entries)
    for code in (
        "agent_conversation_conflict",
        "agent_conversation_format_unsupported",
        "agent_conversation_id_invalid",
        "agent_configuration_error",
    ):
        assert by_code[code]["status"] == "active"
    for code in (
        "llm_provider_error",
        "agent_structured_output_error",
        "guardrail_error",
    ):
        assert by_code[code]["status"] == "reserved"


@pytest.mark.parametrize("count", [0, 1, 2])
def test_status_reports_exact_path_order_and_shadowing(
    binary: Binary, count: int
) -> None:
    import os
    import subprocess

    commands = []
    for index in range(count):
        command = binary.directory / f"bin-{index}" / "neosian"
        command.parent.mkdir()
        command.write_bytes(b"")
        command.chmod(0o700)
        commands.append(str(command))
    env = binary.environment()
    env["PATH"] = os.pathsep.join(
        str(binary.directory / f"bin-{i}") for i in range(count)
    )
    result = subprocess.run(
        [str(binary.executable), "status", "--json"],
        env=env,
        cwd=binary.directory,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    status = json.loads(result.stdout)
    assert status["path_executables"] == commands
    if count < 2:
        assert status["shadowing"] == []
    else:
        assert status["shadowing"] == [
            f"PATH selects {commands[0]}; also found {commands[1]}. Run an absolute path to choose an installation"
        ]
