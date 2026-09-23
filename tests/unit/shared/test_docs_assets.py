"""Docs-as-data (DESIGN §14.4, ECOSYSTEM §8).

Importing the loader runs its fail-fast validation — the CLI imports it
lazily, so this suite is what makes a malformed shipped page fail the
gate instead of a user's `neosian docs` call.
"""

import re
from importlib import resources
from pathlib import Path

from neosian._foundation.shared.docs_assets import (
    _SUMMARY_MAX,
    _TOPICS,
    list_topics,
    load_page,
)


class TestTheShippedPages:
    def test_every_shipped_page_loads(self) -> None:
        pages = list_topics()
        assert len(pages) == len(_TOPICS)
        for page in pages:
            assert page.title
            assert page.summary
            assert page.body

    def test_the_manifest_is_the_order(self) -> None:
        assert tuple(page.topic for page in list_topics()) == _TOPICS

    def test_topology_is_always_shipped(self) -> None:
        assert "topology" in {page.topic for page in list_topics()}

    def test_every_hand_written_topic_list_is_the_manifest(self) -> None:
        """The chat tool's description, its parameter, the resident prompt,
        README and llms.txt each name the topics by hand: a page the wheel
        gains is named in all of them (NW2 found `wire` missing from two)."""
        root = Path(__file__).resolve().parents[3]
        prompts = resources.files("neosian.assets").joinpath("prompts")
        listings = {
            "tools.yaml": prompts.joinpath("tools.yaml").read_text(encoding="utf-8"),
            "chat.yaml": prompts.joinpath("chat.yaml").read_text(encoding="utf-8"),
            "README.md": (root / "README.md").read_text(encoding="utf-8"),
            "llms.txt": (root / "llms.txt").read_text(encoding="utf-8"),
        }
        for name, text in listings.items():
            for topic in _TOPICS:
                assert re.search(rf"\b{topic}\b", text), f"{name} lacks {topic}"

    def test_summaries_are_one_line_and_short(self) -> None:
        for page in list_topics():
            assert "\n" not in page.summary
            assert len(page.summary) <= _SUMMARY_MAX

    def test_bodies_carry_no_frontmatter_delimiter(self) -> None:
        for page in list_topics():
            assert not page.body.startswith("---")

    def test_the_pages_reach_the_package(self) -> None:
        for topic in _TOPICS:
            text = (
                resources.files("neosian.assets")
                .joinpath(f"docs/{topic}.md")
                .read_text(encoding="utf-8")
            )
            assert text.startswith("---")


class TestLookup:
    def test_a_known_topic_loads(self) -> None:
        page = load_page("topology")
        assert page is not None
        assert page.topic == "topology"

    def test_an_unknown_topic_returns_none(self) -> None:
        assert load_page("nope") is None


class TestTopologyContent:
    """The mandatory page carries the 2x2 and the one-writer rule."""

    def test_the_two_axes_are_stated(self) -> None:
        page = load_page("topology")
        assert page is not None
        assert "who runs neosian code" in page.body
        assert "where the bytes live" in page.body

    def test_the_one_writer_rule_is_stated(self) -> None:
        page = load_page("topology")
        assert page is not None
        assert "one writer at a time" in page.body

    def test_the_appliance_quickstart_is_present(self) -> None:
        # NM shipped the state process: the unshipped marker is gone and
        # the page carries the appliance quickstart (§18.9).
        page = load_page("topology")
        assert page is not None
        assert "not yet shipped" not in page.body.replace("\n", " ")
        assert "neosian serve" in page.body
        assert "NEOSIAN_SERVE_TOKEN" in page.body
        assert "RemoteStore.connect" in page.body
