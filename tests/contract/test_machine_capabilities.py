"""The capability statement on the machine document request (DESIGN §40.54
item 18): only `GET {machine_endpoint}` carries it, and a changed value
fetches the document without the ETag kept for the earlier one."""

from __future__ import annotations

import json
import re
import tomllib

from tests.contract.profile_support import run
from tests.contract.support import Binary
from tests.contract.test_machine_login import (
    SECRET,
    TRUST,
    Host,
    headless,
    host_server,
    record,
)

ENV = {**TRUST, "NEOSIAN_LOGIN_TOKEN": SECRET}
VALUE = "turn_records=1"
LINE = f'capabilities = "{VALUE}"\n'


def machine(
    host: Host, since: int = 0
) -> list[tuple[str, str, str | None, str | None]]:
    return [seen for seen in host.seen[since:] if seen[1] == "/machine"]


def edit(binary: Binary, change: str, to: str) -> None:
    state = binary.home / "login" / "state.toml"
    text = state.read_text()
    edited = re.sub(change, to, text, count=1, flags=re.MULTILINE)
    assert edited != text
    state.write_text(edited)


def due(binary: Binary) -> None:
    """The refresh interval has elapsed, without waiting for it."""
    edit(binary, r'^refreshed_at = ".*"$', 'refreshed_at = "2000-01-01T00:00:00Z"')


def saved(binary: Binary) -> dict[str, object]:
    return tomllib.loads((binary.home / "login" / "state.toml").read_text())


def test_only_the_machine_document_request_states_capabilities(
    binary: Binary,
) -> None:
    with host_server() as host:
        host.create = True
        first = run(
            binary,
            "login",
            "--host",
            host.url + "/host",
            "--workspace",
            "none",
            "--json",
            env=TRUST,
        )
        assert first.returncode == 0, first.stderr
        assert run(binary, "memory", "view", "/project", env=ENV).returncode == 0
        refreshed = run(binary, "login", "--refresh", "--json", env=ENV)
        assert json.loads(refreshed.stdout)["updated"] is False
        assert machine(host)[-1] == ("GET", "/machine", VALUE, '"1"')
        due(binary)
        assert record(binary, SECRET, "SessionStart").returncode == 0
        due(binary)
        assert run(binary, "mcp", "--actor", "mcp:contract", env=ENV).returncode == 0
        project = binary.directory / "created"
        project.mkdir()
        created = headless(
            binary, host, SECRET, "--workspace", "new", "--project", str(project)
        )
        assert created.returncode == 0, created.stdout
        assert json.loads(created.stdout)["project"]["profile"] == "created"
        assert run(binary, "logout", "--json", env=ENV).returncode == 0
        # first login, refresh, SessionStart, MCP start, login and its re-fetch
        assert len(machine(host)) == 6
        assert all(seen[0] == "GET" and seen[2] == VALUE for seen in machine(host))
        others = [seen for seen in host.seen if seen[1] != "/machine"]
        assert {seen[1].split("/v1/")[0] for seen in others} == {
            "/host",
            "/device",
            "/token",
            "/workspaces",
            "/logout",
            "/state",
        }
        assert [seen for seen in others if seen[2] is not None] == []


def test_a_changed_value_fetches_the_document_without_the_old_etag(
    binary: Binary,
) -> None:
    with host_server() as host:
        assert headless(binary, host).returncode == 0
        assert machine(host) == [("GET", "/machine", VALUE, None)]
        assert (saved(binary)["capabilities"], saved(binary)["etag"]) == (VALUE, '"1"')
        # A different value under the same ETag, then a state written before
        # the value was kept under an ETag the host no longer sends.
        for seed, etag in (('capabilities = "turn_records=0"\n', '"1"'), ("", '"2"')):
            edit(binary, f"^{re.escape(LINE)}", seed)
            host.etag = etag
            host.label = f"Workspace {etag}"
            before = len(host.seen)
            kept = (binary.home / "login" / "state.toml").read_bytes()
            host.failures = 1
            assert run(binary, "login", "--refresh", env=ENV).returncode == 1
            assert (binary.home / "login" / "state.toml").read_bytes() == kept
            refreshed = run(binary, "login", "--refresh", "--json", env=ENV)
            assert json.loads(refreshed.stdout)["updated"] is True, refreshed.stdout
            assert [seen[3] for seen in machine(host, before)] == [None, None]
            assert (saved(binary)["capabilities"], saved(binary)["etag"]) == (
                VALUE,
                etag,
            )
            shown = run(binary, "profile", "show", "personal", env=ENV)
            assert json.loads(shown.stdout)["definition"]["name"] == host.label
            again = run(binary, "login", "--refresh", "--json", env=ENV)
            assert json.loads(again.stdout)["updated"] is False
            assert machine(host)[-1] == ("GET", "/machine", VALUE, etag)
