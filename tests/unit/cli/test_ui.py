"""The shell's small Rich helpers (DESIGN §30.3): the numbered menu, the
header, the two formatters. The menu reads a piped stdin here."""

import io

import pytest
from rich.console import Console

from neosian._cli.ui import (
    format_args,
    format_elapsed_time,
    load_header,
    pick,
    print_header,
)


def _console() -> tuple[Console, io.StringIO]:
    out = io.StringIO()
    return Console(file=out, width=100, no_color=True), out


@pytest.mark.unit
class TestPick:
    def test_the_number_typed_is_the_index(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("2\n"))
        console, out = _console()
        assert pick(console, "Select Provider:", ["OpenAI", "Anthropic"]) == 1
        text = out.getvalue()
        assert "Select Provider:" in text and "1. OpenAI" in text
        assert "2. Anthropic" in text

    def test_a_number_off_the_menu_asks_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("9\n1\n"))
        console, _ = _console()
        assert pick(console, "Pick:", ["a", "b"]) == 0

    def test_an_empty_answer_takes_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
        console, _ = _console()
        assert pick(console, "Pick:", ["a", "b"], default=1) == 1

    def test_end_of_input_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        console, _ = _console()
        assert pick(console, "Pick:", ["a", "b"]) is None


@pytest.mark.unit
class TestHeader:
    def test_the_art_ships_in_the_wheel(self) -> None:
        assert load_header().plain.strip()

    def test_the_header_names_the_agent_and_the_way_out(self) -> None:
        console, out = _console()
        print_header(console, "my_agent")
        text = out.getvalue()
        assert "Agent: my_agent" in text and "/quit" in text


@pytest.mark.unit
class TestFormatters:
    def test_long_strings_are_clipped_in_the_arguments(self) -> None:
        assert format_args({"text": "x" * 40, "n": 3}) == (
            f"text={'x' * 30 + '...'!r}, n=3"
        )

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(1.234, "Response time: 1.23s"), (83.5, "Response time: 1m 23.50s")],
    )
    def test_elapsed_time(self, seconds: float, expected: str) -> None:
        assert format_elapsed_time(seconds) == expected
