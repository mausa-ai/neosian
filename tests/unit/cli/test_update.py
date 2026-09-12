"""`neosian update` and the knob (DESIGN §30.3, #214): the index on a fake
transport, the stamp, the three fences, the tiers, and the human door —
an agent verb never checks."""

import io
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from neosian import __version__
from neosian._cli.config import set_value
from neosian._cli.shape import CONTAINER, PROJECT, UV_TOOL, Shape
from neosian._cli.update import (
    Version,
    check_on_the_human_door,
    current_mode,
    human_door,
    latest_release,
    plan,
    read_stamp,
    run_update,
)


def _index(*names: str, yanked: str | None = None) -> httpx.MockTransport:
    files = [
        {"filename": f"neosian-{v}-py3-none-any.whl", "yanked": False} for v in names
    ]
    if yanked is not None:
        files.append({"filename": f"neosian-{yanked}-py3-none-any.whl", "yanked": True})

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://pypi.org/simple/neosian/"
        assert request.headers["accept"] == "application/vnd.pypi.simple.v1+json"
        return httpx.Response(200, json={"files": files})

    return httpx.MockTransport(handler)


def _down() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    return httpx.MockTransport(handler)


def _recorder(applied: list[str]) -> Callable[[str], int]:
    def apply(line: str) -> int:
        applied.append(line)
        return 0

    return apply


def _uv_tool(tmp_path: Path) -> Path:
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (prefix / "uv-receipt.toml").write_text("")
    return prefix


class TestVersion:
    def test_parse_and_order(self) -> None:
        assert Version.parse("1.2.3rc4") == Version(1, 2, 3, False, 4)
        assert Version.parse("1.2.3") == Version(1, 2, 3, True, 0)
        assert Version.parse("0.0.1.post1") is None and Version.parse("abc") is None
        rc, final = Version.parse("1.0.0rc9"), Version.parse("1.0.0")
        assert rc is not None and final is not None and rc < final
        assert str(rc) == "1.0.0rc9" and str(final) == "1.0.0"


class TestTheIndex:
    def test_the_newest_unyanked_release(self) -> None:
        latest = latest_release(_index("0.0.1", "1.0.0rc3", "1.0.0rc4", yanked="9.0.0"))
        assert str(latest) == "1.0.0rc4"

    def test_offline_is_silent(self) -> None:
        assert latest_release(_down()) is None

    def test_a_malformed_index_is_silent(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(200, text="not json")

        assert latest_release(httpx.MockTransport(handler)) is None


class TestTheFences:
    def test_current_and_notify(self) -> None:
        latest = Version.parse("1.0.0rc3")
        assert (
            plan("1.0.0rc3", latest, Shape(UV_TOOL), applying=True).action == "current"
        )
        newer = Version.parse("1.0.0rc9")
        notice = plan("1.0.0rc3", newer, Shape(PROJECT), applying=False)
        assert notice.action == "notify" and "uv add neosian==1.0.0rc9" in notice.line

    def test_apply_only_the_uv_tool_shape(self) -> None:
        newer = Version.parse("1.0.1")
        assert plan("1.0.0", newer, Shape(UV_TOOL), applying=True).action == "apply"
        refused = plan("1.0.0", newer, Shape(CONTAINER), applying=True)
        assert refused.action == "refuse" and "container" in refused.line

    def test_never_a_new_major(self) -> None:
        refused = plan("1.4.0", Version.parse("2.0.0"), Shape(UV_TOOL), applying=True)
        assert refused.action == "refuse" and "new major" in refused.line

    def test_never_a_prerelease_over_a_stable(self) -> None:
        refused = plan(
            "1.0.0", Version.parse("1.1.0rc1"), Shape(UV_TOOL), applying=True
        )
        assert refused.action == "refuse" and "pre-release" in refused.line
        # An rc install may move to the next rc.
        assert (
            plan(
                "1.0.0rc3", Version.parse("1.0.0rc4"), Shape(UV_TOOL), applying=True
            ).action
            == "apply"
        )

    def test_unreachable(self) -> None:
        assert (
            plan("1.0.0", None, Shape(UV_TOOL), applying=False).action == "unreachable"
        )


class TestTheVerb:
    def _run(
        self, tmp_path: Path, argv: list[str], transport: httpx.MockTransport
    ) -> tuple[int, str, str, list[str]]:
        applied: list[str] = []

        def apply(command: str) -> int:
            applied.append(command)
            return 0

        out, err = io.StringIO(), io.StringIO()
        code = run_update(
            argv,
            {"NEOSIAN_HOME": str(tmp_path / "home")},
            out=out,
            err=err,
            transport=transport,
            prefix=_uv_tool(tmp_path),
            apply=apply,
        )
        return code, out.getvalue(), err.getvalue(), applied

    def test_check_prints_the_command_and_stamps(self, tmp_path: Path) -> None:
        code, out, _, applied = self._run(tmp_path, ["--json"], _index("99.0.0"))
        assert code == 0 and applied == []
        payload = json.loads(out)
        assert payload["current"] == __version__ and payload["latest"] == "99.0.0"
        assert payload["action"] == "notify" and payload["shape"] == UV_TOOL
        assert payload["line"].endswith("uv tool install neosian==99.0.0")
        stamp = read_stamp(tmp_path / "home")
        assert stamp is not None and stamp.latest == "99.0.0"

    def test_write_applies_within_the_fences(self, tmp_path: Path) -> None:
        major, minor, patch = __version__.split("rc")[0].split(".")
        next_patch = f"{major}.{minor}.{int(patch) + 1}"
        code, out, _, applied = self._run(tmp_path, ["--write"], _index(next_patch))
        assert code == 0
        assert applied == [f"uv tool install neosian=={next_patch}"]
        assert out.strip() == applied[0]

    def test_write_refuses_a_new_major_at_1(self, tmp_path: Path) -> None:
        code, out, _, applied = self._run(tmp_path, ["--write"], _index("99.0.0"))
        assert code == 1 and applied == [] and "new major" in out

    def test_unreachable_is_1(self, tmp_path: Path) -> None:
        code, out, _, _ = self._run(tmp_path, [], _down())
        assert code == 1 and "unreachable" in out

    def test_current_is_0(self, tmp_path: Path) -> None:
        code, out, _, _ = self._run(tmp_path, [], _index(__version__))
        assert code == 0 and "is current" in out

    def test_mode_sets_the_knob(self, tmp_path: Path) -> None:
        code, out, _, _ = self._run(tmp_path, ["--mode", "notify", "--json"], _down())
        assert code == 0 and json.loads(out) == {"mode": "notify"}
        assert (
            run_update(["--mode", "loud"], {}, out=io.StringIO(), err=io.StringIO())
            == 2
        )


class TestTheHumanDoor:
    @pytest.mark.parametrize(
        "verb", ["record", "mcp", "memory", "audit", "serve", "export"]
    )
    def test_an_agent_verb_never_checks(self, verb: str) -> None:
        assert human_door(verb, on_terminal=True, argv=["neosian", verb]) is False

    @pytest.mark.parametrize(
        "verb", [None, "chat", "status", "playground", "configure"]
    )
    def test_the_human_verbs_on_a_terminal_only(self, verb: str | None) -> None:
        assert human_door(verb, on_terminal=True, argv=["neosian"]) is True
        assert human_door(verb, on_terminal=False, argv=["neosian"]) is False
        assert human_door(verb, on_terminal=True, argv=["neosian", "--json"]) is False

    def test_off_never_contacts_the_index(self, tmp_path: Path) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            calls.append(1)
            return httpx.Response(200, json={"files": []})

        env = {"NEOSIAN_HOME": str(tmp_path / "home")}
        err = io.StringIO()
        assert (
            check_on_the_human_door(
                env, err=err, transport=httpx.MockTransport(handler)
            )
            is None
        )
        assert calls == [] and err.getvalue() == ""

    def test_notify_prints_one_line_and_throttles(self, tmp_path: Path) -> None:
        set_value("update", "mode", "notify")
        env = {"NEOSIAN_HOME": str(tmp_path / "home")}
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            calls.append(1)
            return httpx.Response(
                200, json={"files": [{"filename": "neosian-99.0.0-py3-none-any.whl"}]}
            )

        transport = httpx.MockTransport(handler)
        now = datetime(2026, 9, 10, 12, tzinfo=UTC)
        err = io.StringIO()
        decision = check_on_the_human_door(
            env, err=err, transport=transport, now=now, prefix=_uv_tool(tmp_path)
        )
        assert decision is not None and decision.action == "notify"
        assert err.getvalue().count("\n") == 1 and "99.0.0" in err.getvalue()
        # Inside 24 h the stamp answers; the index is not asked again.
        again = check_on_the_human_door(
            env, err=io.StringIO(), transport=_down(), now=now + timedelta(hours=23)
        )
        assert again is not None and again.latest == "99.0.0"
        assert calls == [1]
        # After 24 h it asks again — offline is silent.
        later = check_on_the_human_door(
            env, err=io.StringIO(), transport=_down(), now=now + timedelta(hours=25)
        )
        assert later is None

    def test_auto_applies_and_prints_what_it_did(self, tmp_path: Path) -> None:
        set_value("update", "mode", "auto")
        env = {"NEOSIAN_HOME": str(tmp_path / "home")}
        major, minor, patch = __version__.split("rc")[0].split(".")
        next_patch = f"{major}.{minor}.{int(patch) + 1}"
        applied: list[str] = []
        err = io.StringIO()
        decision = check_on_the_human_door(
            env,
            err=err,
            transport=_index(next_patch),
            prefix=_uv_tool(tmp_path),
            apply=_recorder(applied),
        )
        assert decision is not None and decision.action == "apply"
        assert applied == [f"uv tool install neosian=={next_patch}"]
        assert f"neosian {next_patch}:" in err.getvalue()

    def test_auto_prints_a_new_major_never_applies_it(self, tmp_path: Path) -> None:
        set_value("update", "mode", "auto")
        env = {"NEOSIAN_HOME": str(tmp_path / "home")}
        applied: list[str] = []
        err = io.StringIO()
        decision = check_on_the_human_door(
            env,
            err=err,
            transport=_index("99.0.0"),
            prefix=_uv_tool(tmp_path),
            apply=_recorder(applied),
        )
        assert decision is not None and decision.action == "refuse"
        assert applied == [] and "new major" in err.getvalue()


def test_a_broken_config_leaves_the_knob_off(tmp_path: Path) -> None:
    """EC-11: the check at the human door never crashes the verb behind
    it; that verb names the file when it reads the keys."""
    config = tmp_path / "home" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[update\nmode = 'auto'\n")
    assert current_mode() == "off"
