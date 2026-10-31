"""Explicit update controls stay separate from passive status policy."""

import pytest

from tests.contract.support import Binary


@pytest.mark.parametrize("policy", ["off", "notify", "auto"])
def test_setting_update_policy_only_changes_config(binary: Binary, policy: str) -> None:
    assert binary.json("update", "--mode", policy) == {"mode": policy}
    assert f'mode = "{policy}"' in (binary.home / "config.toml").read_text()
    assert not (binary.home / "binary-update-check").exists()


def test_contradictory_update_flags_fail_before_network_or_install(
    binary: Binary,
) -> None:
    result = binary.run("update", "--check", "--write")
    assert result.returncode == 2
    assert "cannot be used with" in result.stderr
    assert not binary.home.exists()
