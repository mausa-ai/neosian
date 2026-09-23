"""`neosian configure` (DESIGN §30): catalog-driven, non-interactive, keys
from stdin only, `--json` on every form; the provider table behind it
and the key loader, which exports what is stored so a door the shell has
not loaded yet still finds its key."""

import io
import json
from collections.abc import Mapping

import pytest

from neosian._cli.config import (
    get_all_credentials,
    get_config_path,
    set_api_key,
    set_value,
)
from neosian._cli.configure import run_configure
from neosian._cli.providers import (
    find_provider,
    key_source,
    load_keys_into_env,
    provider_keys,
)
from neosian._foundation.shared.catalog import OpenAICompatible
from neosian._foundation.shared.registry import register_model


class _Run:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code, self.out, self.err = code, out, err


def _run(
    argv: list[str],
    env: Mapping[str, str] | None = None,
    *,
    stdin: str = "",
    tty: bool = False,
    prompt: object = None,
) -> _Run:
    out, err = io.StringIO(), io.StringIO()
    code = run_configure(
        argv,
        {} if env is None else env,
        stdin=io.StringIO(stdin),
        out=out,
        err=err,
        tty=tty,
        prompt=prompt,  # type: ignore[arg-type]
    )
    return _Run(code, out.getvalue(), err.getvalue())


class TestTheTable:
    def test_shipped_providers_then_every_shipped_door(self) -> None:
        names = [row.name for row in provider_keys()]
        assert names[:3] == ["openai", "anthropic", "cerebras"]
        assert "xai" in names and "gemini" in names  # no code change per door
        assert find_provider("xai") is not None
        assert find_provider("xai").env == "XAI_API_KEY"  # type: ignore[union-attr]

    def test_the_config_key_is_the_env_name_lowercased(self) -> None:
        row = find_provider("openai")
        assert row is not None and row.key == "openai_api_key"

    def test_a_registered_door_joins_the_table(self) -> None:
        door = OpenAICompatible(name="acme", api_key_env="ACME_API_KEY")
        register_model("acme-1", provider=door, context_window=1, max_output_tokens=1)
        assert [r.env for r in provider_keys() if r.name == "acme"] == ["ACME_API_KEY"]

    def test_a_keyless_door_has_no_row(self) -> None:
        door = OpenAICompatible(
            name="local", api_key_env=None, base_url="http://127.0.0.1:8080/v1"
        )
        register_model(
            "gemma-4-e4b-it", provider=door, context_window=1, max_output_tokens=1
        )
        assert "local" not in [row.name for row in provider_keys()]

    def test_key_source_names_env_over_file_never_the_value(self) -> None:
        row = find_provider("openai")
        assert row is not None
        assert key_source(row, {}) is None
        set_api_key(row.key, "sk-file")
        assert key_source(row, {}) == "file"
        assert key_source(row, {"OPENAI_API_KEY": "sk-env"}) == "env"

    def test_the_loader_fills_only_what_the_environment_lacks(self) -> None:
        set_api_key("openai_api_key", "sk-file")
        set_api_key("xai_api_key", "xai-file")
        env = {"OPENAI_API_KEY": "sk-env"}
        load_keys_into_env(env)
        assert env == {"OPENAI_API_KEY": "sk-env", "XAI_API_KEY": "xai-file"}

    def test_the_loader_exports_a_key_for_a_door_not_loaded_yet(self) -> None:
        # The agent file that registers `acme` loads AFTER the loader runs
        # (chat, playground, eval): its key has to be there already.
        assert find_provider("acme") is None  # no such door in this process
        set_api_key("acme_api_key", "acme-file")
        set_value("credentials", "not an env name", "junk")  # hand-edited
        env: dict[str, str] = {}
        load_keys_into_env(env)
        assert env == {"ACME_API_KEY": "acme-file"}

    def test_a_stored_key_outside_the_table_is_a_row_by_its_env_name(self) -> None:
        set_api_key("acme_api_key", "acme-file")
        (row,) = [r for r in provider_keys() if r.env == "ACME_API_KEY"]
        assert row.name == "ACME_API_KEY" and key_source(row, {}) == "file"
        assert [r.env for r in provider_keys()].count("XAI_API_KEY") == 1

    def test_a_broken_file_adds_no_rows_and_does_not_raise(self) -> None:
        # `status` reports a broken config as a finding and still lists keys.
        path = get_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[credentials\n")
        names = [r.name for r in provider_keys()]
        assert names[:3] == ["openai", "anthropic", "cerebras"]


class TestForms:
    def test_list_is_the_default_under_a_pipe(self) -> None:
        set_api_key("cerebras_api_key", "gsk")
        result = _run([], {"OPENAI_API_KEY": "sk"})
        assert result.code == 0
        lines = result.out.splitlines()
        assert lines[0].split() == ["openai", "OPENAI_API_KEY", "env"]
        assert lines[2].split() == ["cerebras", "CEREBRAS_API_KEY", "file"]
        assert "gsk" not in result.out and "sk" not in lines[0]
        assert lines[-1] == f"config: {get_config_path()}"

    def test_list_json_is_one_object(self) -> None:
        result = _run(["--list", "--json"])
        payload = json.loads(result.out)
        assert result.out.count("\n") == 1
        assert payload["config_path"] == str(get_config_path())
        assert {"name": "gemini", "env": "GEMINI_API_KEY", "source": None} in payload[
            "providers"
        ]

    def test_a_key_is_read_from_stdin(self) -> None:
        result = _run(["--provider", "xai", "--key", "-"], stdin="xai-secret\n")
        assert result.code == 0, result.err
        assert result.out == f"saved xai (XAI_API_KEY) in {get_config_path()}\n"
        assert get_all_credentials() == {"xai_api_key": "xai-secret"}

    def test_a_key_in_argv_is_refused_and_nothing_written(self) -> None:
        result = _run(["--provider", "xai", "--key", "xai-secret"])
        assert result.code == 2
        assert "stdin" in result.err
        assert not get_config_path().exists()

    def test_an_empty_stdin_exits_1(self) -> None:
        result = _run(["--provider", "xai", "--key", "-"], stdin="\n")
        assert result.code == 1 and "empty key" in result.err

    def test_a_door_the_shell_has_not_loaded_is_named_by_its_env(self) -> None:
        saved = _run(["--env", "ACME_API_KEY", "--key", "-", "--json"], stdin="k-1\n")
        assert saved.code == 0 and json.loads(saved.out)["env"] == "ACME_API_KEY"
        assert get_all_credentials()["acme_api_key"] == "k-1"
        listed = json.loads(_run(["--list", "--json"]).out)["providers"]
        ours = {"name": "ACME_API_KEY", "env": "ACME_API_KEY", "source": "file"}
        assert ours in listed
        assert "k-1" not in _run(["--list"]).out  # never the value
        dropped = _run(["--delete", "--env", "ACME_API_KEY", "--json"])
        assert json.loads(dropped.out) == {"deleted": "ACME_API_KEY", "existed": True}
        assert "acme_api_key" not in get_all_credentials()

    def test_an_env_the_table_knows_is_that_provider(self) -> None:
        saved = _run(["--env", "XAI_API_KEY", "--key", "-", "--json"], stdin="x\n")
        assert json.loads(saved.out)["saved"] == "xai"

    @pytest.mark.parametrize(
        "argv",
        [
            ["--env", "acme_api_key", "--key", "-"],  # not an env name
            ["--env", "ACME_API_KEY", "--provider", "xai", "--key", "-"],
            ["--env", "ACME_API_KEY"],  # neither --key - nor --delete
            ["--env", "ACME_API_KEY", "--key", "k-in-argv"],
        ],
    )
    def test_a_malformed_env_form_exits_2_and_writes_nothing(
        self, argv: list[str]
    ) -> None:
        assert _run(argv, stdin="k\n").code == 2
        assert not get_config_path().exists()

    def test_an_unknown_provider_exits_2_naming_the_table(self) -> None:
        result = _run(["--provider", "nope", "--key", "-"], stdin="x")
        assert result.code == 2
        assert "anthropic" in result.err and "xai" in result.err

    @pytest.mark.parametrize("argv", [["--key", "-"], ["--provider", "xai"]])
    def test_half_forms_exit_2(self, argv: list[str]) -> None:
        assert _run(argv, stdin="x").code == 2

    def test_delete_one_key_json(self) -> None:
        set_api_key("xai_api_key", "x")
        set_api_key("openai_api_key", "o")
        result = _run(["--delete", "--provider", "xai", "--json"])
        assert json.loads(result.out) == {"deleted": "xai", "existed": True}
        assert get_all_credentials() == {"openai_api_key": "o"}
        again = _run(["--delete", "--provider", "xai"])
        assert again.code == 0 and again.out == "no key for xai\n"

    def test_delete_the_file(self) -> None:
        set_api_key("xai_api_key", "x")
        result = _run(["--delete"])
        assert result.code == 0 and result.out.startswith("deleted ")
        assert not get_config_path().exists()

    def test_bare_on_a_terminal_prompts_each_provider(self) -> None:
        set_api_key("openai_api_key", "sk-old-key-1234")
        asked: list[str] = []

        def prompt(label: str) -> str:
            asked.append(label)
            return "new-cerebras" if label.startswith("cerebras") else ""

        result = _run([], tty=True, prompt=prompt)
        assert result.code == 0
        assert (
            asked[0] == "openai (OPENAI_API_KEY) [sk-o****234]"
        )  # masked, never whole
        assert "saved cerebras" in result.out
        assert get_all_credentials() == {
            "openai_api_key": "sk-old-key-1234",
            "cerebras_api_key": "new-cerebras",
        }

    def test_the_typer_stub_forwards_verbatim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import click
        import typer

        from neosian._cli import configure as engine
        from neosian._cli.main import configure

        seen: list[list[str]] = []

        def fake(argv: list[str], env: object, **_: object) -> int:
            del env
            seen.append(list(argv))
            return 0

        monkeypatch.setattr(engine, "run_configure", fake)
        ctx = click.Context(click.Command("configure"))
        ctx.args = ["--list", "--json"]
        with pytest.raises(typer.Exit) as excinfo:
            configure(ctx)  # type: ignore[arg-type]
        assert excinfo.value.exit_code == 0
        assert seen == [["--list", "--json"]]
