"""Skill input, revision and authority boundaries, using independent literal records."""

from __future__ import annotations

import json
import os

import pytest

from tests.contract.knowledge_support import marked, success
from tests.contract.profile_support import PROFILE, bind, grant_env, install, run
from tests.contract.schema import validate_schema
from tests.contract.skill_support import SKILL, invoke, package, sha
from tests.contract.support import ASSETS, Binary, serve


@pytest.mark.parametrize(
    "change",
    [
        {"format": 2},
        {"unknown": True},
        {"requires": [{"skill": "../escape"}]},
        {
            "requires": [
                {
                    "skill": "builtin:upkeep",
                    "revision": {"version": 1, "sha256": "0" * 64},
                }
            ]
        },
        {
            "requires": [
                {"skill": "review", "revision": {"version": 0, "sha256": "0" * 64}}
            ]
        },
        {"files": {"../escape": "0" * 64}},
        {"files": {"references/reason.md": "0" * 64}},
        {"files": {"references/reason.md": "BAD"}},
        {"files": {".collection": "0" * 64}},
        {"collections": ["messages/private"]},
        {"collections": ["decisions", "decisions"]},
    ],
)
def test_invalid_manifests_fail_before_opening_storage(
    binary: Binary, change: dict[str, object]
) -> None:
    source = package(binary.directory / "package")
    manifest = source / "skill.json"
    data = json.loads(manifest.read_text())
    data.update(change)
    manifest.write_text(json.dumps(data))
    assert (
        invoke(
            binary, "import", str(source), "--to", "/memories/skills/review", "--write"
        ).returncode
        != 0
    )
    assert not binary.home.exists()


@pytest.mark.parametrize(
    "path",
    [
        "/memories/notes/review",
        "/memories/skills",
        "/memories/skills/../handoff",
        "/memories/skills/.collection",
        "/memories/skills/review/",
        "/memories/skills/a b",
    ],
)
def test_import_requires_a_skill_address(binary: Binary, path: str) -> None:
    source = package(binary.directory / "package")
    assert (
        invoke(binary, "import", str(source), "--to", path, "--write").returncode != 0
    )
    assert not binary.home.exists()


@pytest.mark.parametrize(
    "bad", [b"\xff", b"---\ndescription: d\n---\nNUL\x00", b"not a skill"]
)
def test_invalid_root_body_is_refused(binary: Binary, bad: bytes) -> None:
    file = binary.directory / "skill.md"
    file.write_bytes(bad)
    assert (
        invoke(
            binary, "import", str(file), "--to", "/memories/skills/review", "--write"
        ).returncode
        != 0
    )
    assert not binary.home.exists()


def test_duplicate_json_keys_and_unlisted_files_refuse(binary: Binary) -> None:
    source = package(binary.directory / "package")
    manifest = source / "skill.json"
    original = manifest.read_text()
    manifest.write_text(original.replace('"format": 1', '"format": 1, "format": 1'))
    assert (
        invoke(
            binary, "import", str(source), "--to", "/memories/skills/review"
        ).returncode
        != 0
    )
    manifest.write_text(original)
    (source / "unlisted.md").write_text("must be declared")
    assert (
        invoke(
            binary, "import", str(source), "--to", "/memories/skills/review"
        ).returncode
        != 0
    )
    assert not binary.home.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs Windows privileges")
def test_symlinks_refuse_and_scripts_remain_text_data(binary: Binary) -> None:
    source = package(binary.directory / "package")
    (source / "skill.json").unlink()
    (source / "link.md").symlink_to(source / "SKILL.md")
    path = "/memories/skills/review"
    assert invoke(binary, "import", str(source), "--to", path).returncode != 0
    (source / "link.md").unlink()
    sentinel = binary.directory / "EXECUTED"
    (source / "do.sh").write_text(f"touch '{sentinel}'\n")
    (source / "do.sh").chmod(0o755)
    success(invoke(binary, "import", str(source), "--to", path, "--write"))
    success(invoke(binary, "load", path))
    output = binary.directory / "out"
    success(invoke(binary, "export", path, "--output", str(output)))
    assert not sentinel.exists()
    assert (output / "do.sh").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("mode", ["ro", "eo"])
def test_mount_modes_apply_to_import_and_edits(binary: Binary, mode: str) -> None:
    path = "/project/skills/review"
    flags = ["--mount", f"scope=user:skills,path=project,{mode}"]
    file = binary.directory / "skill.md"
    file.write_text(SKILL)
    assert (
        invoke(
            binary, "import", str(file), "--to", path, "--write", flags=flags
        ).returncode
        != 0
    )
    with serve(binary) as server:
        server.post(
            "memory/write", scope="user:skills", path="skills/review", content=SKILL
        )
        success(invoke(binary, "load", path, flags=flags))
        result = invoke(
            binary,
            "edit",
            path,
            "--file",
            str(file),
            "--expect-version",
            "1",
            "--write",
            flags=flags,
        )
        assert (result.returncode == 0) == (mode == "eo")
        resource = package(binary.directory / "package")
        assert (
            invoke(
                binary,
                "import",
                str(resource),
                "--to",
                path,
                "--replace",
                "--write",
                flags=flags,
            ).returncode
            != 0
        )
        assert (
            server.post(
                "memory/read",
                scope="user:skills",
                path="skills/review/references/reason.md",
            )["document"]
            is None
        )


def test_profile_pins_validate_identity_and_shape(binary: Binary) -> None:
    install(binary)
    bind(binary, "--root", str(binary.home))
    source = package(binary.directory / "package")
    flags = ["--profile", "portable"]
    path = "/project/skills/review"
    success(invoke(binary, "import", str(source), "--to", path, "--write", flags=flags))
    success(invoke(binary, "pin", path, "--write", flags=flags))
    encoded = run(binary, "profile", "export", "portable").stdout
    import tomllib

    validate_schema(
        json.loads((ASSETS / "profile-v1.schema.json").read_text()),
        tomllib.loads(encoded),
    )
    validate_schema(
        json.loads((ASSETS / "skill-v1.schema.json").read_text()),
        json.loads((source / "skill.json").read_text()),
    )
    for old, new in [
        ("version = 1", "version = 0"),
        ('source = "knowledge"\nscope', 'source = "unknown"\nscope'),
        ('scope = "workspace:portable"', 'scope = "workspace:forged"'),
    ]:
        # Change only the pin table, keeping the mount definition intact.
        prefix, pins = encoded.split("[skill_pins.", 1)
        file = binary.directory / "invalid.toml"
        file.write_text(prefix + "[skill_pins." + pins.replace(old, new, 1))
        assert (
            run(binary, "profile", "import", "bad", str(file), "--write").returncode
            != 0
        )


def test_dependency_revision_and_missing_deleted_root(binary: Binary) -> None:
    with serve(binary) as server:
        server.post(
            "memory/write", scope="user:skills", path="skills/evidence", content=SKILL
        )
        source = package(
            binary.directory / "package",
            requires=[
                {"skill": "evidence", "revision": {"version": 1, "sha256": sha(SKILL)}}
            ],
        )
        path = "/memories/skills/review"
        success(invoke(binary, "import", str(source), "--to", path, "--write"))
        server.post(
            "memory/write", scope="user:skills", path="skills/evidence", content=SKILL
        )
        assert "revision changed" in invoke(binary, "load", path).stdout
        server.post("memory/delete", scope="user:skills", path="skills/review")
        assert (
            invoke(
                binary,
                "revert",
                path,
                "--version",
                "1",
                "--expect-version",
                "1",
                "--write",
            ).returncode
            != 0
        )
        assert (
            server.post("memory/read", scope="user:skills", path="skills/review")[
                "document"
            ]
            is None
        )


def test_remote_grants_defeat_forged_profile_and_skill_dependencies(
    binary: Binary,
) -> None:
    env = grant_env(binary.directory / "grants.toml")
    with serve(binary, extra_env=env) as server:
        root = marked(
            SKILL,
            collection="skills",
            attributes={
                "skill": {"format": 1, "requires": [{"skill": "/other/skills/secret"}]}
            },
        )
        server.post(
            "memory/write",
            scope="workspace:portable",
            path="skills/review",
            content=root,
        )
        server.post(
            "memory/write",
            scope="workspace:other",
            path="skills/secret",
            content=SKILL.replace("reasoning", "UNAUTHORIZED"),
        )
        text = PROFILE.replace(
            'skills = ["/project/skills/review"]',
            'skills = ["/project/skills/review", "/other/skills/secret"]',
        )
        text += '\n[[mounts]]\nsource = "knowledge"\nscope = "workspace:other"\npath = "other"\naccess = "rw"\n'
        install(binary, text)
        bind(binary, "--url", server.url, "--token-env", "SKILL_TOKEN")
        result = invoke(
            binary,
            "load",
            "/project/skills/review",
            flags=["--profile", "portable"],
            env={"SKILL_TOKEN": "reader-token"},
        )
        assert (
            result.returncode != 0
            and "UNAUTHORIZED" not in result.stdout + result.stderr
        )
        file = binary.directory / "skill.md"
        file.write_text(SKILL)
        result = invoke(
            binary,
            "edit",
            "/project/skills/review",
            "--file",
            str(file),
            "--expect-version",
            "1",
            "--write",
            flags=["--profile", "portable"],
            env={"SKILL_TOKEN": "reader-token"},
        )
        assert result.returncode != 0
        assert (
            server.post(
                "memory/read", scope="workspace:portable", path="skills/review"
            )["document"]["version"]
            == 1
        )
