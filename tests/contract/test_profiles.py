"""Portable profiles, bounded context, explicit sources and safe local selection."""

from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

import pytest

from tests.contract.profile_support import PROFILE, bind, install, memory, rpc, run
from tests.contract.schema import validate_schema
from tests.contract.support import ASSETS, Binary


def test_preview_import_export_and_local_binding(binary: Binary) -> None:
    preview = binary.run("profile", "init", "portable")
    assert preview.returncode == 0
    assert json.loads(preview.stdout)["definition"]["capture"]["mode"] == "off"
    assert not binary.home.exists()
    imported = install(binary)
    assert imported["activated"] is False
    store = binary.directory / "machine-only-data"
    preview = binary.run(
        "profile",
        "bind",
        "portable",
        "--source",
        "knowledge",
        "--root",
        str(store),
        "--project",
        str(binary.directory),
    )
    assert preview.returncode == 0
    assert not (binary.home / "profile-bindings.toml").exists()
    bind(binary, "--root", str(store), "--project", str(binary.directory))
    assert not store.exists()
    exported = binary.run("profile", "export", "portable")
    assert tomllib.loads(exported.stdout) == imported["definition"]
    validate_schema(
        json.loads((ASSETS / "profile-v1.schema.json").read_text()),
        imported["definition"],
    )
    assert str(store) not in exported.stdout
    assert "machine-only" not in exported.stdout
    shown = binary.run("profile", "show")
    assert json.loads(shown.stdout)["ready"] is True
    assert not store.exists()
    status = binary.json("status")
    assert status["profile"]["workspace_id"] == imported["definition"]["workspace_id"]
    assert not store.exists()
    result = binary.json("memory", "create", "/project/decision", "--content", "shared")
    assert result["success"] is True
    assert "shared" in memory(binary, "view", "/project/decision")["data"]


@pytest.mark.parametrize(
    "change",
    [
        ("profile_format = 1", "profile_format = 2"),
        (
            'name = "Portable knowledge"',
            'name = "Portable knowledge"\ntoken = "PRIVATE"',
        ),
        (
            'workspace_id = "11111111-2222-4333-8444-555555555555"',
            'workspace_id = "bad"',
        ),
        ('access = "rw"', 'access = "admin"'),
        ('injection = "index"', 'injection = "full"'),
        ('path = "project"', 'path = "../outside"'),
        ('sources = ["knowledge"]', 'sources = ["knowledge", "knowledge"]'),
    ],
)
def test_invalid_profiles_leave_no_configuration(
    binary: Binary, change: tuple[str, str]
) -> None:
    path = binary.directory / "input.toml"
    path.write_text(PROFILE.replace(*change), encoding="utf-8")
    result = binary.run("profile", "import", "portable", str(path), "--write")
    assert result.returncode == 2
    assert "PRIVATE" not in result.stdout + result.stderr
    assert not binary.home.exists()


def test_selection_precedence_and_conflicts_fail_closed(binary: Binary) -> None:
    install(binary)
    bind(
        binary,
        "--root",
        str(binary.directory / "data"),
        "--project",
        str(binary.directory),
    )
    missing = run(
        binary, "memory", "view", "/", "--json", env={"NEOSIAN_PROFILE": "absent"}
    )
    assert missing.returncode == 2
    explicit = run(
        binary,
        "memory",
        "view",
        "/",
        "--profile",
        "portable",
        "--json",
        env={"NEOSIAN_PROFILE": "absent"},
    )
    assert explicit.returncode == 0
    for extra in [("--root", str(binary.home)), ("--scope", "user:escape")]:
        result = binary.run("memory", "view", "/", *extra, "--json")
        assert result.returncode == 2
    for env in [{"NEOSIAN_SCOPE": "user:escape"}, {"NEOSIAN_POSTGRES_DSN": "PRIVATE"}]:
        result = run(binary, "memory", "view", "/", "--json", env=env)
        assert result.returncode == 2
        assert "PRIVATE" not in result.stdout + result.stderr
    assert binary.run("export", str(binary.directory / "archive")).returncode == 2


def test_explicit_sources_skills_and_bounded_context(binary: Binary) -> None:
    text = PROFILE.replace(
        'sources = ["knowledge"]', 'sources = ["knowledge", "personal"]'
    )
    text += """
[[mounts]]
source = "personal"
scope = "user:private"
path = "personal"
access = "ro"
injection = "none"

[context]
max_chars = 1024
"""
    install(binary, text)
    data = binary.directory / "data"
    private = binary.directory / "personal"
    bind(binary, "--root", str(data))
    bind(binary, "--root", str(private), source="personal")
    skill = "---\nname: review\ndescription: Check the portable facts\n---\nUse the shared rules."
    memory(binary, "create", "/project/skills/review", "--content", skill)
    memory(
        binary,
        "create",
        "/project/skills/hidden",
        "--content",
        skill.replace("review", "hidden"),
    )
    memory(
        binary, "create", "/project/long", "--content", "BODY-SHOULD-NOT-INJECT" * 100
    )
    answers = rpc(
        binary,
        [
            ("list_skills", {}),
            ("load_skill", {"name": "review"}),
            ("load_skill", {"name": "/project/skills/review"}),
            ("memory", {"command": "create", "path": "/personal/x", "content": "no"}),
        ],
    )
    context = answers[0]["result"]["instructions"]
    assert len(context) <= 1024
    assert "BODY-SHOULD-NOT-INJECT" not in context
    assert "source=knowledge" in context and "scope=workspace:portable" in context
    tools = {t["name"] for t in answers[1]["result"]["tools"]}
    assert not {"recall_turn", "search_history", "continue_session", "handoff"} & tools
    skills = ast.literal_eval(answers[2]["result"]["content"][0]["text"])
    assert [s["name"] for s in skills] == ["/project/skills/review"]
    assert skills[0]["source"] == "knowledge" and skills[0]["version"] == 1
    assert answers[3]["result"]["isError"] is True
    assert "Use the shared rules" in answers[4]["result"]["content"][0]["text"]
    assert answers[5]["result"]["isError"] is True


def test_full_context_omits_whole_oversize_documents(binary: Binary) -> None:
    install(
        binary,
        PROFILE.replace(
            'injection = "index"', 'injection = "full"\ndocuments = ["small", "large"]'
        )
        + "\n[context]\nmax_chars = 700\n",
    )
    bind(binary, "--root", str(binary.directory / "data"))
    memory(binary, "create", "/project/small", "--content", "portable body")
    memory(binary, "create", "/project/large", "--content", "TOO-LARGE" * 1000)
    context = rpc(binary)[0]["result"]["instructions"]
    assert "portable body" in context
    assert "TOO-LARGE" not in context
    assert "omitted" in context.lower()
    assert len(context) <= 700


@pytest.mark.parametrize(
    "client", ["claude-code", "codex", "opencode", "muse-code", "cursor", "pi"]
)
def test_setup_profiles_preview_without_secrets(binary: Binary, client: str) -> None:
    install(binary)
    bind(binary, "--url", "https://memory.invalid", "--token-env", "TEAM_MEMORY_TOKEN")
    target = binary.directory / client
    target.mkdir()
    result = run(
        binary,
        "setup",
        "--profile",
        "portable",
        "--client",
        client,
        "--at",
        f"{client}={target}",
        "--json",
        env={"TEAM_MEMORY_TOKEN": "PRIVATE"},
    )
    assert result.returncode == 0, result.stderr
    assert "PRIVATE" not in result.stdout + result.stderr
    row = json.loads(result.stdout)["clients"][0]
    assert row["profile"]["capture"] == "off"
    assert "--profile" in json.dumps(row) and "portable" in json.dumps(row)
    assert "--spool" not in json.dumps(row)
    assert not list(target.iterdir())


def test_profiles_refuse_symlink_configuration(binary: Binary, tmp_path: Path) -> None:
    binary.home.mkdir()
    (binary.home / "profiles").symlink_to(tmp_path, target_is_directory=True)
    result = binary.run("profile", "init", "portable", "--write")
    assert result.returncode == 2
    assert not (tmp_path / "portable.toml").exists()
