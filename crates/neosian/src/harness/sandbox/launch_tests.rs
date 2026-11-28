//! The profile text, the platform decision, the launch's presets and, on
//! macOS, the kernel's answer to the probe, inside a scratch directory only.

use std::path::Path;

use super::tests::{Fixture, unset};
use super::*;

#[test]
fn macos_gets_the_common_policy_and_elsewhere_keeps_the_presets_and_says_so() {
    assert_eq!(platform_for("macos"), Platform::Common);
    for os in ["linux", "freebsd", "windows"] {
        assert_eq!(platform_for(os), Platform::Presets);
    }
    assert_eq!(note(Platform::Common, Agent::Codex, Mode::ReadOnly), None);
    let said = note(Platform::Presets, Agent::Codex, Mode::ReadOnly).unwrap();
    assert!(said.contains("only on macOS") && said.contains("codex") && said.contains("read-only"));
}

#[test]
fn a_missing_facility_refuses_and_starts_nothing() {
    let missing = Path::new("/nonexistent/sandbox-exec");
    let fixture = Fixture::new("sandbox-missing");
    let refused = macos::probe(missing, "(version 1)", &fixture.path("private.probe"));
    let refused = refused.unwrap_err();
    assert!(
        refused.contains("/nonexistent/sandbox-exec") && refused.contains("not started"),
        "{refused}"
    );
}

#[cfg(target_os = "macos")]
#[test]
fn the_kernel_refuses_the_probe_write_or_the_turn_is_refused() {
    let fixture = Fixture::new("sandbox-kernel");
    let facility = Path::new(macos::FACILITY);
    let policy = fixture.build(Agent::Codex, Mode::Write, &unset).unwrap();
    let profile = policy.profile();
    let target = macos::target(&policy, &fixture.path("private"), &fixture.path("home"));
    let target = target.unwrap();
    assert_eq!(macos::probe(facility, &profile, &target), Ok(()));
    let open = "(version 1)(allow default)";
    let refused = macos::probe(facility, open, &target).unwrap_err();
    assert!(refused.contains("did not refuse"), "{refused}");
    assert!(!target.exists(), "the landed probe write is removed");
    let broken = macos::probe(facility, "(version 1)(no-such-rule)", &target).unwrap_err();
    assert!(broken.contains("could not apply"), "{broken}");
}

#[test]
fn a_preset_changes_only_on_a_confined_launch_and_only_for_codex() {
    use super::super::adapter::{Request, plan};
    let request = |agent, mode, confined| Request {
        agent,
        mode,
        model: None,
        directory: Path::new("/work/dir"),
        resume: None,
        new_session: "5eab6b3f-d0af-49c3-9c0c-a9b47c2c8ebe",
        private: Path::new("/tmp/neosian-harness-x"),
        codex_keys: &[],
        opencode_config: None,
        parent: None,
        server: None,
        codex_off: &[],
        confined,
    };
    for mode in [Mode::ReadOnly, Mode::Write] {
        for agent in [Agent::ClaudeCode, Agent::OpenCode] {
            assert_eq!(
                plan(&request(agent, mode, true)),
                plan(&request(agent, mode, false))
            );
        }
        let today = plan(&request(Agent::Codex, mode, false)).unwrap().argv;
        let confined = plan(&request(Agent::Codex, mode, true)).unwrap().argv;
        assert!(today.contains(&"approval_policy=\"never\"".to_string()));
        assert!(!today.iter().any(|w| w.starts_with("--dangerously")));
        let without = |argv: &[String]| -> Vec<String> {
            let words = [
                "-s",
                "read-only",
                "workspace-write",
                "-c",
                super::super::codex::OUTER_POLICY,
            ];
            argv.iter()
                .filter(|w| {
                    !words.contains(&w.as_str())
                        && !w.starts_with("approval_policy")
                        && !w.starts_with("sandbox_")
                })
                .cloned()
                .collect()
        };
        assert!(confined.contains(&super::super::codex::OUTER_POLICY.to_string()));
        assert!(
            !confined
                .iter()
                .any(|w| w == "-s" || w.starts_with("approval_policy"))
        );
        assert_eq!(without(&today), without(&confined), "{mode:?}");
    }
}

#[test]
fn the_profile_denies_writes_first_and_protects_last() {
    let fixture = Fixture::new("sandbox-profile");
    let profile = fixture
        .build(Agent::OpenCode, Mode::Write, &unset)
        .unwrap()
        .profile();
    assert!(
        profile.starts_with("(version 1)\n(allow default)\n(deny file-write*)\n(allow file-write*")
    );
    let allow = profile.find("(allow file-write*").unwrap();
    let deny = profile.rfind("(deny file-write*\n").unwrap();
    let locks = profile.find("(allow file-write-data\n").unwrap();
    assert!(allow < deny && deny < locks);
    assert!(profile.contains("com.apple.cfprefsd.daemon"));
}
