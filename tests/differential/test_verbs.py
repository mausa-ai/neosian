"""The ratchet: the verbs the binary lists are exactly the verbs the
harness asserts on, so a verb the binary gains un-skips its cases."""

from pathlib import Path

from tests.differential.harness import BINARY_VERBS, binary_verbs


def test_the_binary_verbs_are_the_asserted_ones(rust_door: Path) -> None:
    assert binary_verbs(rust_door) == BINARY_VERBS
