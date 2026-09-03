"""`kind: memory` suite loading — strict keys, loud failures (DESIGN §13.12)."""

import re
import textwrap
from pathlib import Path

import pytest

from neosian._foundation.evaluation.loader import load_eval_config
from neosian._foundation.evaluation.memory_types import (
    MemoryEvalConfig,
    Transport,
)
from neosian._foundation.evaluation.types import EvalKind, MatchMode
from neosian._foundation.memory.mounts import Mount
from neosian._foundation.shared.exceptions import (
    EvalCaseInvalidError,
    EvalConfigInvalidYAMLError,
    EvalConfigMissingKeyError,
    EvalConfigUnknownKeyError,
    MemoryStoreError,
)

MINIMAL = """
kind: memory
name: suite
agent: agent.py
models: [fake]
mounts:
  - scope: user:eval
    mount_path: user
scenarios:
  - name: s
    sessions:
      - name: one
        turns:
          - user: hi
            expect: {tool: memory}
        script:
          - tool_calls:
              - name: memory
                arguments: {command: view, path: /user}
          - content: done
        expect_store:
          counts: {/user: 0}
"""


def _write(tmp_path: Path, body: str, filename: str = "suite.yaml") -> Path:
    path = tmp_path / filename
    path.write_text(textwrap.dedent(body))
    return path


def _load(tmp_path: Path, body: str) -> MemoryEvalConfig:
    config = load_eval_config(_write(tmp_path, body))
    assert isinstance(config, MemoryEvalConfig)
    return config


@pytest.mark.unit
class TestSuiteLevel:
    def test_minimal_memory_suite_parses(self, tmp_path: Path) -> None:
        config = _load(tmp_path, MINIMAL)
        assert config.kind is EvalKind.MEMORY
        assert config.name == "suite"
        assert config.mounts == (Mount(scope="user:eval", mount_path="user"),)
        assert config.transports == (Transport.FUNCTION,)
        assert config.stop_on_failure is True
        assert config.throttle_ms == 500
        assert config.case_names == ("s",)
        assert config.variant_names == ("function",)

    def test_session_reflect_parses_and_defaults_off(self, tmp_path: Path) -> None:
        config = _load(tmp_path, MINIMAL)
        assert config.scenarios[0].sessions[0].reflect is False
        body = MINIMAL.replace("- name: one\n", "- name: one\n        reflect: true\n")
        config = _load(tmp_path, body)
        assert config.scenarios[0].sessions[0].reflect is True

    def test_session_reflect_must_be_a_boolean(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "- name: one\n", "- name: one\n        reflect: always\n"
        )
        with pytest.raises(EvalCaseInvalidError, match="'reflect' must be a boolean"):
            load_eval_config(_write(tmp_path, body))

    def test_kind_is_read_before_the_key_check(self, tmp_path: Path) -> None:
        # `cases:` is an agent-kind key — the memory branch must own the
        # rejection, with its migration hint.
        body = MINIMAL + "cases: []\n"
        with pytest.raises(
            EvalConfigUnknownKeyError, match="memory suites use 'scenarios:'"
        ):
            load_eval_config(_write(tmp_path, body))

    def test_variants_hint(self, tmp_path: Path) -> None:
        with pytest.raises(EvalConfigUnknownKeyError, match="compare 'transports:'"):
            load_eval_config(_write(tmp_path, MINIMAL + "variants: []\n"))

    def test_mounts_and_scenarios_are_required(self, tmp_path: Path) -> None:
        body = "kind: memory\nname: n\nagent: a.py\nmodels: [fake]\n"
        with pytest.raises(EvalConfigMissingKeyError):
            load_eval_config(_write(tmp_path, body))

    def test_transports_parse(self, tmp_path: Path) -> None:
        config = _load(
            tmp_path, MINIMAL + "transports: [function, native_memory, http]\n"
        )
        assert config.transports == (
            Transport.FUNCTION,
            Transport.NATIVE,
            Transport.HTTP,
        )

    def test_unknown_transport(self, tmp_path: Path) -> None:
        with pytest.raises(EvalConfigInvalidYAMLError, match="unknown transport 'sse'"):
            load_eval_config(_write(tmp_path, MINIMAL + "transports: [sse]\n"))

    def test_duplicate_transport(self, tmp_path: Path) -> None:
        with pytest.raises(EvalConfigInvalidYAMLError, match="duplicate transport"):
            load_eval_config(
                _write(tmp_path, MINIMAL + "transports: [function, function]\n")
            )


@pytest.mark.unit
class TestMounts:
    def test_bad_scope_surfaces_in_the_eval_family(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("scope: user:eval", "scope: 'not a scope!'")
        with pytest.raises(EvalConfigInvalidYAMLError, match="mount 1") as excinfo:
            load_eval_config(_write(tmp_path, body))
        # The store error is the cause, never the raised type (§13.12).
        assert isinstance(excinfo.value.__cause__, MemoryStoreError)

    def test_multi_segment_mount_path_refused(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("mount_path: user", "mount_path: user/sub")
        with pytest.raises(EvalConfigInvalidYAMLError, match="mount 1"):
            load_eval_config(_write(tmp_path, body))

    def test_unknown_mount_key(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("mount_path: user", "mount_path: user\n    ro: true")
        with pytest.raises(EvalConfigUnknownKeyError):
            load_eval_config(_write(tmp_path, body))

    def test_duplicate_mount_paths(self, tmp_path: Path) -> None:
        extra = "  - scope: user:other\n    mount_path: user\n"
        body = MINIMAL.replace("scenarios:", extra + "scenarios:")
        with pytest.raises(
            EvalConfigInvalidYAMLError, match="mount paths must be unique"
        ):
            load_eval_config(_write(tmp_path, body))


@pytest.mark.unit
class TestScenarios:
    def test_duplicate_scenario_names(self, tmp_path: Path) -> None:
        scenario = MINIMAL[MINIMAL.index("  - name: s") :]
        with pytest.raises(
            EvalConfigInvalidYAMLError, match="duplicate scenario name 's'"
        ):
            load_eval_config(_write(tmp_path, MINIMAL + scenario))

    def test_duplicate_session_names(self, tmp_path: Path) -> None:
        session = MINIMAL[MINIMAL.index("      - name: one") :]
        with pytest.raises(EvalCaseInvalidError, match="duplicate session name"):
            load_eval_config(_write(tmp_path, MINIMAL + session))

    def test_scenario_level_input_gets_the_sessions_hint(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("    sessions:", "    input: hi\n    sessions:")
        with pytest.raises(EvalCaseInvalidError, match=r"sessions\[\].turns"):
            load_eval_config(_write(tmp_path, body))

    def test_half_scripted_scenario_refused(self, tmp_path: Path) -> None:
        extra = """\
      - name: two
        turns:
          - user: again
            expect: {tool: memory}
"""
        with pytest.raises(EvalCaseInvalidError, match="every session or none"):
            load_eval_config(_write(tmp_path, MINIMAL + extra))

    def test_script_turns_parse_to_fake_turns(self, tmp_path: Path) -> None:
        config = _load(tmp_path, MINIMAL)
        script = config.scenarios[0].sessions[0].script
        assert script is not None
        assert str(script[0].tool_calls[0].id) == "script_0_0"
        assert config.scenarios[0].is_scripted


@pytest.mark.unit
class TestStoreExpectations:
    def test_full_expectation_parses(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "          counts: {/user: 0}",
            """\
          counts: {/user: 1}
          absent: [/user/gone]
          forbidden: [sk-secret]
          documents:
            - path: /user/prefs
              content: {contains: espresso}
              versions: 2
              actions: [created, modified]""",
        )
        expect = _load(tmp_path, body).scenarios[0].sessions[0].expect_store
        assert expect.counts == {"/user": 1}
        assert expect.absent == ("/user/gone",)
        assert expect.forbidden == ("sk-secret",)
        document = expect.documents[0]
        assert document.path == "/user/prefs"
        assert document.content[0].mode is MatchMode.CONTAINS
        assert document.versions == 2
        assert document.actions == ("created", "modified")

    def test_unmounted_path_refused(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("counts: {/user: 0}", "counts: {/nowhere: 0}")
        with pytest.raises(EvalCaseInvalidError, match="names no declared mount"):
            load_eval_config(_write(tmp_path, body))

    def test_document_path_must_reach_inside_the_mount(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("counts: {/user: 0}", "documents: [{path: /user}]")
        with pytest.raises(EvalCaseInvalidError, match="must name a document inside"):
            load_eval_config(_write(tmp_path, body))

    def test_versions_actions_disagreement(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "counts: {/user: 0}",
            "documents: [{path: /user/x, versions: 2, actions: [created]}]",
        )
        with pytest.raises(EvalCaseInvalidError, match="disagree"):
            load_eval_config(_write(tmp_path, body))

    def test_unknown_action(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "counts: {/user: 0}",
            "documents: [{path: /user/x, actions: [redacted]}]",
        )
        with pytest.raises(EvalCaseInvalidError, match="unknown action"):
            load_eval_config(_write(tmp_path, body))

    def test_unknown_store_key(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("counts:", "totals:")
        with pytest.raises(
            EvalCaseInvalidError, match="expect_store: unknown key 'totals'"
        ):
            load_eval_config(_write(tmp_path, body))

    def test_path_prefix_parses_and_may_be_the_bare_mount(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "counts: {/user: 0}",
            "documents: [{path_prefix: /user, content: {contains: espresso}}]",
        )
        document = (
            _load(tmp_path, body).scenarios[0].sessions[0].expect_store.documents[0]
        )
        assert document.path is None
        assert document.path_prefix == "/user"
        assert document.content[0].mode is MatchMode.CONTAINS

    def test_path_and_path_prefix_are_exclusive(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "counts: {/user: 0}",
            "documents: [{path: /user/x, path_prefix: /user}]",
        )
        with pytest.raises(
            EvalCaseInvalidError, match="exactly one of 'path' or 'path_prefix'"
        ):
            load_eval_config(_write(tmp_path, body))

    def test_document_without_either_key_refused(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "counts: {/user: 0}",
            "documents: [{content: {contains: espresso}}]",
        )
        with pytest.raises(
            EvalCaseInvalidError, match="exactly one of 'path' or 'path_prefix'"
        ):
            load_eval_config(_write(tmp_path, body))

    def test_path_prefix_must_name_a_mount(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "counts: {/user: 0}", "documents: [{path_prefix: /nowhere}]"
        )
        with pytest.raises(EvalCaseInvalidError, match="names no declared mount"):
            load_eval_config(_write(tmp_path, body))


_SEED = """\
    seed:
      - path: /user/coffee
        content: espresso
      - path: /user/fresh
        content: new
        age_days: 0
"""


@pytest.mark.unit
class TestSeed:
    def test_seed_parses_with_the_default_age(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("    sessions:", _SEED + "    sessions:")
        seed = _load(tmp_path, body).scenarios[0].seed
        assert seed[0].path == "/user/coffee"
        assert seed[0].content == "espresso"
        assert seed[0].age_days == 30
        assert seed[1].age_days == 0

    def test_no_seed_is_an_empty_tuple(self, tmp_path: Path) -> None:
        assert _load(tmp_path, MINIMAL).scenarios[0].seed == ()

    def test_empty_seed_list_refused(self, tmp_path: Path) -> None:
        body = MINIMAL.replace("    sessions:", "    seed: []\n    sessions:")
        with pytest.raises(EvalCaseInvalidError, match="'seed' must be a non-empty"):
            load_eval_config(_write(tmp_path, body))

    def test_unknown_seed_key(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "    sessions:",
            "    seed: [{path: /user/x, content: c, actor: me}]\n    sessions:",
        )
        with pytest.raises(EvalCaseInvalidError, match="seed 1: unknown key 'actor'"):
            load_eval_config(_write(tmp_path, body))

    def test_seed_requires_path_and_content(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "    sessions:", "    seed: [{path: /user/x}]\n    sessions:"
        )
        with pytest.raises(EvalCaseInvalidError, match="seed 1 missing 'content'"):
            load_eval_config(_write(tmp_path, body))

    def test_seed_path_must_reach_inside_a_declared_mount(self, tmp_path: Path) -> None:
        for path in ("/nowhere/x", "/user"):
            body = MINIMAL.replace(
                "    sessions:",
                f"    seed: [{{path: {path}, content: c}}]\n    sessions:",
            )
            with pytest.raises(EvalCaseInvalidError, match="declared\n?\\s*mount"):
                load_eval_config(_write(tmp_path, body))

    def test_negative_or_boolean_age_refused(self, tmp_path: Path) -> None:
        for age in ("-1", "true"):
            body = MINIMAL.replace(
                "    sessions:",
                f"    seed: [{{path: /user/x, content: c, age_days: {age}}}]"
                "\n    sessions:",
            )
            with pytest.raises(EvalCaseInvalidError, match="'age_days' must be"):
                load_eval_config(_write(tmp_path, body))

    def test_duplicate_seed_path_refused(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "    sessions:",
            "    seed: [{path: /user/x, content: a}, {path: /user/x, content: b}]"
            "\n    sessions:",
        )
        with pytest.raises(EvalCaseInvalidError, match="duplicate seed path"):
            load_eval_config(_write(tmp_path, body))


@pytest.mark.unit
class TestMaintain:
    def test_maintain_parses_and_defaults_off(self, tmp_path: Path) -> None:
        assert _load(tmp_path, MINIMAL).scenarios[0].sessions[0].maintain is False
        body = MINIMAL.replace("- name: one\n", "- name: one\n        maintain: true\n")
        assert _load(tmp_path, body).scenarios[0].sessions[0].maintain is True

    def test_maintain_must_be_a_boolean(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "- name: one\n", "- name: one\n        maintain: weekly\n"
        )
        with pytest.raises(EvalCaseInvalidError, match="'maintain' must be a boolean"):
            load_eval_config(_write(tmp_path, body))

    def test_a_maintain_session_needs_no_turns(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "      - name: one",
            """\
      - name: garden
        maintain: true
        script:
          - content: '{"ops": []}'
        expect_store:
          counts: {/user: 0}
      - name: one""",
        )
        session = _load(tmp_path, body).scenarios[0].sessions[0]
        assert session.maintain is True
        assert session.turns == ()

    def test_turns_stay_required_without_maintain(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "      - name: one",
            """\
      - name: bare
        script:
          - content: done
      - name: one""",
        )
        with pytest.raises(EvalCaseInvalidError, match="session missing 'turns'"):
            load_eval_config(_write(tmp_path, body))

    def test_reflect_without_turns_refused(self, tmp_path: Path) -> None:
        body = MINIMAL.replace(
            "      - name: one",
            """\
      - name: garden
        maintain: true
        reflect: true
        script:
          - content: '{"ops": []}'
      - name: one""",
        )
        with pytest.raises(EvalCaseInvalidError, match="reflects nothing"):
            load_eval_config(_write(tmp_path, body))


_RECORD_SUITE = """
kind: memory
name: suite
agent: agent.py
models: [fake]
mounts:
  - scope: user:eval
    mount_path: user
scenarios:
  - name: cross
    sessions:
      - name: writes
        record:
          agent: claude-code
          session_id: cc-1
          prompt: "Add a retry."
          stop: "Done."
          {tools}
        {extra}
      - name: reads
        session_start: true
        turns:
          - user: what changed?
            expect: {{tool: recall_turn}}
        script:
          - tool_calls:
              - name: recall_turn
                arguments: {{turn: 1, conversation: cc-1}}
          - content: ok
"""


def _record_suite(tools: str = "", extra: str = "") -> str:
    return _RECORD_SUITE.format(tools=tools, extra=extra)


class TestRecordSessions:
    """§21.7: a foreign agent's session in the pack — replayed, never
    scripted; `session_start` on the session that reads it."""

    def test_a_record_session_parses(self, tmp_path: Path) -> None:
        tools = "tools: [{name: Edit, input: {file_path: f.py}, response: ok}]"
        config = _load(tmp_path, _record_suite(tools))
        writes, reads = config.scenarios[0].sessions
        assert writes.record is not None and writes.turns == ()
        assert writes.record.agent == "claude-code"
        assert writes.record.session_id == "cc-1"
        assert writes.record.tools[0].name == "Edit"
        assert writes.record.tools[0].input == {"file_path": "f.py"}
        assert writes.record.tools[0].id is None
        assert reads.session_start is True and reads.record is None
        # The all-or-none script rule ignores the record session.
        assert config.scenarios[0].is_scripted

    def test_tools_default_empty(self, tmp_path: Path) -> None:
        config = _load(tmp_path, _record_suite())
        assert config.scenarios[0].sessions[0].record is not None
        assert config.scenarios[0].sessions[0].record.tools == ()

    @pytest.mark.parametrize(
        "extra", ["turns: []", "script: []", "session_start: true", "maintain: true"]
    )
    def test_a_record_session_has_nothing_an_agent_session_has(
        self, tmp_path: Path, extra: str
    ) -> None:
        key = extra.split(":")[0]
        with pytest.raises(EvalCaseInvalidError, match=re.escape(f"drop ['{key}']")):
            _load(tmp_path, _record_suite(extra=extra))

    @pytest.mark.parametrize(
        ("tools", "reason"),
        [
            ("tools: [{name: Edit, verb: x}]", "tools\\[1\\]"),
            ("tools: {name: Edit}", "'tools' must be a list"),
            ("tools: [{name: 3}]", "wrong type"),
        ],
    )
    def test_bad_tools_refused(self, tmp_path: Path, tools: str, reason: str) -> None:
        with pytest.raises(EvalCaseInvalidError, match=reason):
            _load(tmp_path, _record_suite(tools))

    def test_the_verbs_own_validation_applies(self, tmp_path: Path) -> None:
        with pytest.raises(EvalCaseInvalidError, match="record: "):
            _load(tmp_path, _record_suite().replace("cc-1", "a/b"))
        with pytest.raises(EvalCaseInvalidError, match="record: "):
            _load(tmp_path, _record_suite().replace("claude-code", "Claude Code"))
        with pytest.raises(EvalCaseInvalidError, match="unknown key 'model'"):
            _load(
                tmp_path,
                _record_suite(extra="").replace("stop:", "model: x\n          stop:"),
            )

    def test_session_start_must_be_a_boolean(self, tmp_path: Path) -> None:
        body = _record_suite().replace(
            "session_start: true", "session_start: yes-please"
        )
        with pytest.raises(
            EvalCaseInvalidError, match="'session_start' must be a boolean"
        ):
            _load(tmp_path, body)
