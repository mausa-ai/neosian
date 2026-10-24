"""The live verifier keeps MSI syntax and release expectations independent."""

import runpy
import subprocess
from pathlib import Path

import pytest

from scripts import check_live_release as release


@pytest.mark.parametrize("returncode", [0, 3010])
def test_msi_property_values_with_spaces_use_installer_syntax(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    destination = tmp_path / "msi installation"
    commands: list[str] = []

    def run(command: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["shell"] is False
        commands.append(command)
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(subprocess, "run", run)
    package = tmp_path / "package with spaces.msi"
    release.run_msi(package, "i", tmp_path / "install.log", {}, destination)
    release.run_msi(package, "x", tmp_path / "remove.log", {})
    assert f'/i "{package}"' in commands[0]
    assert f' APPLICATIONFOLDER="{destination}"' in commands[0]
    assert '"APPLICATIONFOLDER=' not in commands[0]
    assert f'/x "{package}"' in commands[1]
    assert "APPLICATIONFOLDER=" not in commands[1]


@pytest.mark.parametrize("failure", ["timeout", "exit"])
def test_msi_failure_preserves_the_installer_log_in_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    log = tmp_path / "install.log"

    def run(command: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        log.write_text("installer failure detail", encoding="utf-16")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 180)
        return subprocess.CompletedProcess(command, 1603)

    monkeypatch.setattr(subprocess, "run", run)
    error = subprocess.TimeoutExpired if failure == "timeout" else RuntimeError
    with pytest.raises(error):
        release.run_msi(tmp_path / "package.msi", "i", log, {})
    assert "installer failure detail" in capsys.readouterr().out


def test_verifier_uses_the_selected_release_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Cargo.toml").write_text('[workspace.package]\nversion = "9.8.7"\n')
    monkeypatch.setenv("NEOSIAN_VERIFY_ROOT", str(tmp_path))
    loaded = runpy.run_path(release.__file__)
    assert loaded["ROOT"] == tmp_path.resolve()
    assert loaded["VERSION"] == "9.8.7"
