"""`neosian docs` — the docs door (DESIGN §14.4, §14.1 exit tiers)."""

import io
import json

import pytest
import typer

from neosian._cli.docs import run_docs
from neosian._cli.main import docs
from neosian._foundation.shared.docs_assets import list_topics, load_page


def _run(topic: str | None, *, json_output: bool = False) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = run_docs(topic, json_output=json_output, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class TestListing:
    def test_no_topic_lists_every_topic_with_a_summary(self) -> None:
        code, out, _ = _run(None)
        assert code == 0
        for page in list_topics():
            assert page.topic in out
            assert page.summary in out

    def test_the_listing_hint_lands_on_stderr(self) -> None:
        _, out, err = _run(None)
        assert "hint:" not in out
        assert "hint: neosian docs <topic>" in err

    def test_json_listing_is_exactly_one_object(self) -> None:
        code, out, err = _run(None, json_output=True)
        assert code == 0
        assert err == ""
        assert out.count("\n") == 1
        payload = json.loads(out)
        assert [t["topic"] for t in payload["topics"]] == [
            p.topic for p in list_topics()
        ]


class TestPage:
    def test_a_known_topic_prints_the_body_verbatim(self) -> None:
        page = load_page("topology")
        assert page is not None
        code, out, err = _run("topology")
        assert code == 0
        assert out == page.body + "\n"
        assert err == ""

    def test_json_page_carries_the_body(self) -> None:
        page = load_page("cli")
        assert page is not None
        code, out, _ = _run("cli", json_output=True)
        assert code == 0
        assert out.count("\n") == 1
        payload = json.loads(out)
        assert payload["topic"] == "cli"
        assert payload["body"] == page.body


class TestUnknownTopic:
    def test_exits_2_with_the_topic_list_on_stderr(self) -> None:
        code, out, err = _run("nope")
        assert code == 2
        assert out == ""
        assert "error: unknown topic 'nope'" in err
        assert "topology" in err

    def test_json_still_exits_2_as_text(self) -> None:
        # §14.1: --json governs the executed tiers only; argv-tier
        # errors stay text on stderr — the tiering, not drift.
        code, out, err = _run("nope", json_output=True)
        assert code == 2
        assert out == ""
        assert "error:" in err


class TestTheTyperStub:
    def test_the_stub_returns_the_engine_code(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(typer.Exit) as excinfo:
            docs("nope")
        assert excinfo.value.exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "error:" in captured.err

    def test_the_stub_prints_a_page(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(typer.Exit) as excinfo:
            docs("topology")
        assert excinfo.value.exit_code == 0
        page = load_page("topology")
        assert page is not None
        assert capsys.readouterr().out == page.body + "\n"
