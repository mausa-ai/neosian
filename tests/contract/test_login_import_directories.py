"""After the host reports a successful import, `neosian login` refreshes the
account's assignments and binds imported projects back to their directories
(the L1d slice of the L1 plan), against the fake host of
`login_import_host.py`.

The refresh fetches the whole machine document. The directory login runs
for is bound exactly to the workspace its historical scope
(`user:<login>/proj:<name>`) was imported to; any other directory resolves
through the same rule the first time it is used. An explicit `--profile`,
`NEOSIAN_PROFILE` and a registered binding come first. A target the commit
did not send or the account is not assigned binds nothing and is named. A
run that dies after the host's success is finished by the next login, which
sends nothing to the import endpoint. Setup is offered after the binding.
No local path reaches the host.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from tests.contract.login_import_host import ImportHost
from tests.contract.profile_support import run
from tests.contract.support import Binary
from tests.contract.test_login_import import (
    AREA,
    hosting,
    interrupted,
    journal,
    last,
    login,
    routes,
    seed,
    sent,
    source,
    unix,
)
from tests.contract.test_login_output import ANSI, clients, terminal
from tests.contract.test_machine_login import TRUST

DEMO = "user:contract/proj:demo"
OTHER = "user:contract/proj:other"


def inside(binary: Binary, name: str) -> Binary:
    """The binary run from a directory `name`, with a home of its own."""
    directory = binary.directory / name
    directory.mkdir(parents=True)
    return Binary(binary.executable, directory)


def scope(binary: Binary, name: str) -> None:
    made = binary.run(
        "memory", "create", "/memories/a", "--scope", name, "--content", "o"
    )
    assert made.returncode == 0, made.stderr


def selected(
    binary: Binary, directory: Path, *more: str, env: dict[str, str] | None = None
) -> Any:
    """The profile `status` selects in `directory`, over `binary`'s home."""
    result = subprocess.run(
        [str(binary.executable), "status", "--json", *more],
        cwd=directory,
        env={**binary.environment(), **(env or {})},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)["profile"]["profile"]


def imported(binary: Binary) -> Any:
    state = tomllib.loads((binary.home / "login" / "state.toml").read_text())
    return state.get("imported")


def target(report: dict[str, Any], name: str) -> Any:
    return next(t["profile"] for t in report["targets"] if t["source"] == name)


def flat(text: str) -> str:
    return " ".join(text.split())


def machine(host: ImportHost) -> list[tuple[str | None, str | None]]:
    """Each machine document request: its capability value and ETag."""
    return [(s[2], s[3]) for s in host.seen if s[1] == "/machine"]


def crashed(binary: Binary, host: ImportHost) -> None:
    """A `login --import` killed while it asks for the machine document
    after the host's success, before anything is installed."""
    process = subprocess.Popen(
        [str(binary.executable), "login", "--host", host.url + "/host"]
        + ["--workspace", "none", "--import"],
        cwd=binary.directory,
        env={**binary.environment(), **TRUST},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert host.held.wait(60)
    finally:
        process.kill()
        process.wait(timeout=10)
        host.release.set()
    assert journal(binary)["stage"] == "succeeded"
    assert not (binary.home / AREA / "snapshot").exists()
    assert imported(binary) is None


def test_the_directory_is_bound_after_the_hosts_success(binary: Binary) -> None:
    demo = inside(binary, "demo")
    before = seed(demo)
    with hosting() as host:
        result = login(demo, host, "--workspace", "none", "--import", "--json")
        assert result.returncode == 0, result.stderr
        # The first install, then the refresh: the whole document, stated.
        assert machine(host) == [("turn_records=1", None)] * 2
        root = str(demo.directory.resolve())
        assert all(root.encode() not in e.raw and root not in e.path for e in host.log)
        assert len(sent(host)) == 5
    report = last(result)["import"]
    workspace = target(report, DEMO)
    assert (report["finished"], report["kept"], report["unbound"]) == (True, False, [])
    assert report["directory"] == {
        "root": root,
        "scope": DEMO,
        "profile": workspace,
        "bound": "import",
    }
    assert last(result)["managed"] == ["personal", workspace]
    said = flat(result.stderr)
    assert f"{root} now uses {workspace}, where {DEMO} was imported." in said
    assert "Run neosian login --refresh" not in said
    assert imported(demo) == {
        "scopes": {DEMO: workspace},
        "directories": {root: workspace},
    }
    assert selected(demo, demo.directory) == workspace
    assert not (demo.home / AREA).exists()
    assert source(demo) == before


def test_another_directory_resolves_the_first_time_it_is_used(binary: Binary) -> None:
    demo = inside(binary, "demo")
    seed(demo)
    scope(demo, OTHER)
    with hosting() as host:
        result = login(demo, host, "--workspace", "none", "--import", "--json")
        assert result.returncode == 0, result.stderr
    report = last(result)["import"]
    other = binary.directory / "elsewhere" / "other"
    (other / "src").mkdir(parents=True)
    plain = binary.directory / "plain"
    plain.mkdir()
    state = (demo.home / "login" / "state.toml").read_bytes()
    assert selected(demo, other) == target(report, OTHER)
    # Below it, a directory's own name decides, and none was imported.
    assert selected(demo, other / "src") == "personal"
    assert selected(demo, plain) == "personal"
    # Resolving writes nothing.
    assert (demo.home / "login" / "state.toml").read_bytes() == state
    assert "from the first time an agent starts there" in flat(result.stderr)


def test_a_manual_binding_and_an_explicit_profile_are_kept(binary: Binary) -> None:
    demo = inside(binary, "demo")
    seed(demo)
    scope(demo, OTHER)
    bindings = demo.home / "profile-bindings.toml"
    with hosting() as host:
        first = login(demo, host, "--workspace", "none", "--json")
        assert first.returncode == 0, first.stderr
        bind = ("profile", "bind", "personal", "--project", str(demo.directory))
        assert demo.run(*bind, "--write").returncode == 0
        projects = tomllib.loads(bindings.read_text())["projects"]
        result = login(demo, host, "--import", "--json")
        assert result.returncode == 0, result.stderr
    report = last(result)["import"]
    assert report["directory"]["bound"] == "kept"
    assert report["directory"]["profile"] == "personal"
    assert "keeps its binding to personal." in flat(result.stderr)
    assert tomllib.loads(bindings.read_text())["projects"] == projects
    assert "directories" not in imported(demo)
    assert selected(demo, demo.directory) == "personal"
    other = binary.directory / "other"
    other.mkdir()
    assert selected(demo, other) == target(report, OTHER)
    assert selected(demo, other, "--profile", "personal") == "personal"
    assert selected(demo, other, env={"NEOSIAN_PROFILE": "personal"}) == "personal"


def test_two_directories_with_one_name_share_its_workspace(binary: Binary) -> None:
    first = inside(binary, "a/demo")
    twin = binary.directory / "b" / "demo"
    twin.mkdir(parents=True)
    seed(first)
    with hosting() as host:
        result = login(first, host, "--workspace", "none", "--import", "--json")
        assert result.returncode == 0, result.stderr
    workspace = target(last(result)["import"], DEMO)
    assert selected(first, first.directory) == workspace
    assert selected(first, twin) == workspace
    said = flat(result.stderr)
    assert "directories with the same name shared one scope and now share" in said
    assert "run neosian profile bind WORKSPACE --project DIR --write." in said
    # The manual bind path keeps one of them apart, and wins.
    bind = ("profile", "bind", "personal", "--project", str(twin), "--write")
    assert first.run(*bind).returncode == 0
    assert selected(first, twin) == "personal"
    assert selected(first, first.directory) == workspace


def test_a_missing_or_removed_assignment_binds_nothing(binary: Binary) -> None:
    demo = inside(binary, "demo")
    seed(demo)
    scope(demo, OTHER)
    other = binary.directory / "other"
    other.mkdir()
    with hosting() as host:
        host.withhold = {"w-4"}
        result = login(demo, host, "--workspace", "none", "--import", "--json")
        assert result.returncode == 0, result.stderr
        report = last(result)["import"]
        assert target(report, DEMO) == "w-4"
        assert report["unbound"] == [
            {"source": DEMO, "profile": "w-4", "reason": "unassigned"}
        ]
        assert report["directory"]["bound"] == "none"
        assert (
            f"{DEMO} is bound to no directory: the host named w-4, which this "
            "account is not assigned now." in flat(result.stderr)
        )
        assert imported(demo) == {"scopes": {OTHER: "w-5"}}
        assert selected(demo, demo.directory) == "personal"
        assert selected(demo, other) == "w-5"
        # Removed later: the refresh drops it and the default serves again.
        host.names, host.etag = ("personal",), '"9"'
        refreshed = run(demo, "login", "--refresh", env=TRUST)
        assert refreshed.returncode == 0, refreshed.stderr
        assert "No longer assigned: w-5" in refreshed.stderr
    assert imported(demo) is None
    assert selected(demo, other) == "personal"


def test_an_unrelated_account_gets_no_binding(binary: Binary) -> None:
    demo = inside(binary, "demo")
    seed(demo)
    with hosting(hold_machine=2) as host:
        crashed(demo, host)
        host.account, mark = "account-two", len(host.log)
        other = login(demo, host, "--json")
        assert other.returncode == 1 and "different account" in other.stdout
        gone = run(demo, "logout", "--forget", env=TRUST)
        assert gone.returncode == 0
        assert "succeeded and its directories are not bound yet" in flat(gone.stderr)
        signed = login(demo, host, "--workspace", "none", "--json")
        assert signed.returncode == 1 and "another account" in signed.stdout
        assert routes(host, mark) == [] and journal(demo)["stage"] == "succeeded"
        assert not (demo.home / "login" / "state.toml").exists()
        assert login(demo, host, "--discard-import").returncode == 0
        fresh = login(demo, host, "--workspace", "none", "--json")
        assert fresh.returncode == 0, fresh.stderr
        # Account two is assigned a profile of the same name, yet the
        # directory keeps its default: the map was account one's.
        assert "w-4" in last(fresh)["managed"]
    assert imported(demo) is None
    assert selected(demo, demo.directory) == "personal"


def test_a_failed_import_binds_nothing(binary: Binary) -> None:
    demo = inside(binary, "demo")
    before = seed(demo)
    with hosting(result="failed") as host:
        result = login(demo, host, "--workspace", "none", "--import", "--json")
        assert result.returncode == 1, result.stderr
        assert len(machine(host)) == 1
    report = last(result)["import"]
    assert report["status"] == "failed" and "finished" not in report
    assert imported(demo) is None
    assert selected(demo, demo.directory) == "personal"
    assert not (demo.home / AREA).exists()
    assert source(demo) == before


def test_a_crash_after_the_hosts_success_is_finished_by_the_next_login(
    binary: Binary,
) -> None:
    demo = inside(binary, "demo")
    before = seed(demo)
    with hosting(hold_machine=2) as host:
        crashed(demo, host)
        mark = len(host.log)
        again = login(demo, host, "--import", "--json")
        assert again.returncode == 0, again.stderr
        assert routes(host, mark) == []
        assert [e.path for e in host.log[mark:]] == ["/host", "/machine"]
        assert len(host.sessions) == 1 and len(host.intents) == 1
    report = last(again)["import"]
    assert last(again)["event"] == "refreshed"
    assert (report["finished"], report["directory"]["bound"]) == (True, "import")
    assert selected(demo, demo.directory) == report["directory"]["profile"]
    assert not (demo.home / AREA).exists()
    assert source(demo) == before


@unix
def test_setup_is_offered_only_after_the_directory_is_bound(binary: Binary) -> None:
    demo = inside(binary, "demo")
    seed(demo)
    env = {**TRUST, **clients(demo, True)}
    argv = ["login", "--host", "", "--workspace", "none", "--import", "--verbose"]
    agents = [(r"Connect your agents now\? \[y/N\]", "y\n"), (r"q cancels:", "\n")]
    with hosting() as host:
        argv[2] = host.url + "/host"
        code, raw = terminal(demo, argv, env, agents)
    assert code == 0, raw
    # Words only: how a line wraps depends on the temporary path's length.
    text = flat(ANSI.sub("", raw))
    # Setup resolved this directory to the bound workspace, never the default.
    assert "profile personal capture" not in text
    bound = text.index("now uses w-4")
    asked = text.index("Agents Connect your agents now?")
    assert bound < asked < text.index("profile w-4 capture")


def test_a_host_that_does_not_vary_by_the_capability_header(binary: Binary) -> None:
    demo = inside(binary, "demo")
    seed(demo)
    with hosting(varies=False) as host:
        result = login(demo, host, "--workspace", "none", "--import", "--json")
        assert result.returncode == 0, result.stderr
        # Its ETag did not change with the new workspaces; the refresh after
        # the success sends none, so it cannot be answered 304.
        assert host.etag == '"1"' and "w-4" in host.names
        assert machine(host) == [("turn_records=1", None)] * 2
        assert selected(demo, demo.directory) == "w-4"
        again = run(demo, "login", "--refresh", "--json", env=TRUST)
        assert json.loads(again.stdout)["updated"] is False
        assert machine(host)[-1] == ("turn_records=1", '"1"')
    assert selected(demo, demo.directory) == "w-4"


def test_refresh_and_logout_say_an_import_is_waiting(binary: Binary) -> None:
    seed(binary)
    with hosting() as host:
        interrupted(binary, host)
        mark = len(host.log)
        refreshed = run(binary, "login", "--refresh", env=TRUST)
        assert refreshed.returncode == 0, refreshed.stderr
        said = flat(refreshed.stderr)
        assert f"An import to {host.url}/host is waiting; this refresh" in said
        assert "Resume the import with neosian login --import, or discard" in said
        assert routes(host, mark) == []
        out = run(binary, "logout", env=TRUST)
        assert out.returncode == 0, out.stderr
        said = flat(out.stderr)
        assert "is waiting. Logging out revokes the credential it belongs to" in said
        assert "run neosian login --discard-import to remove its local copy" in said
        assert routes(host, mark) == []
    assert journal(binary)["stage"] == "transfer"
