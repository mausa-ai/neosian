"""Deterministic upkeep keeps recent, redacted, protected and oldest content."""

import json
import subprocess
from typing import Any

import pytest

from tests.contract.support import ADMIN, Binary, fixture, serve


@pytest.mark.parametrize("remote", [False, True])
def test_maintenance_preview_policy_and_audited_deletes(
    binary: Binary, remote: bool
) -> None:
    with serve(binary) as server:
        base = fixture("records.json")["document"]
        rows: list[dict[str, Any]] = []
        for path, content, updated, redacted in [
            ("a", "same", "2020-01-01T00:00:00Z", False),
            ("b", "same", "2020-01-01T00:00:00Z", False),
            ("empty", " \n\t", "2020-01-01T00:00:00Z", False),
            ("fresh", "", "2999-01-01T00:00:00Z", False),
            ("erased", "", "2020-01-01T00:00:00Z", True),
            ("messages/state", "", "2020-01-01T00:00:00Z", False),
            ("message-links/state", "", "2020-01-01T00:00:00Z", False),
        ]:
            rows.append(
                {
                    **base,
                    "scope": "user:upkeep",
                    "path": path,
                    "content": content,
                    "created_at": "2019-01-01T00:00:00Z",
                    "updated_at": updated,
                    "redacted": redacted,
                }
            )
        for scope in ("user:upkeep", "user:readonly", "user:editonly"):
            server.post(
                "store/restore_scope",
                scope=scope,
                documents=[{**row, "scope": scope} for row in rows],
                versions=[],
                redactions=[],
            )
        args = [
            "memory",
            "maintain",
            "--json",
            "--actor",
            "cli:gardener",
            "--mount",
            "scope=user:upkeep,path=memories",
            "--mount",
            "scope=user:readonly,path=ro,ro",
            "--mount",
            "scope=user:editonly,path=eo,eo",
        ]
        if remote:
            args += ["--url", server.url]
        else:
            args += ["--root", str(binary.home)]
        env = binary.environment()
        env["NEOSIAN_CLIENT_TOKEN"] = ADMIN

        def run(*more: str) -> Any:
            result = subprocess.run(
                [str(binary.executable), *args, *more],
                env=env,
                cwd=binary.directory,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)

        preview = run("--dry-run")
        assert preview["dry_run"] is True and preview["writes"] == []
        assert {row["path"] for row in preview["actions"]} == {
            "/memories/b",
            "/memories/empty",
        }
        done = run()
        assert done["actions"] == preview["actions"]
        assert {row["path"] for row in done["writes"]} == {
            "/memories/b",
            "/memories/empty",
        }
        for path in ("a", "fresh", "erased", "messages/state", "message-links/state"):
            assert server.post("memory/read", scope="user:upkeep", path=path)[
                "document"
            ]
        for scope in ("user:readonly", "user:editonly"):
            assert (
                len(server.post("memory/list_documents", scope=scope)["entries"]) == 7
            )
        versions = server.post("memory/versions", scope="user:upkeep", path="b")[
            "versions"
        ]
        assert versions[0]["action"] == "deleted"
        assert versions[0]["actor"].endswith("cli:gardener")
        assert run()["writes"] == []


@pytest.mark.parametrize("days", ["-1", "365001", "invalid"])
def test_bad_age_builds_no_home(binary: Binary, days: str) -> None:
    result = binary.run("memory", "maintain", "--min-age-days", days)
    assert result.returncode == 2
    assert not binary.home.exists()
