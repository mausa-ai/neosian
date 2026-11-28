//! The policy as data and its exact paths, inside a scratch directory only.

use std::ffi::OsString;
use std::path::{Path, PathBuf};

use super::policy::{Rule, escape, resolve, text};
use super::*;
use crate::testing::Scratch;

pub(super) struct Fixture {
    scratch: Scratch,
}

impl Fixture {
    pub(super) fn new(name: &str) -> Self {
        let scratch = Scratch::new(name);
        for dir in ["home/work", "private", "home/.neosian"] {
            std::fs::create_dir_all(scratch.path().join(dir)).unwrap();
        }
        Self { scratch }
    }

    pub(super) fn path(&self, relative: &str) -> PathBuf {
        self.scratch.path().canonicalize().unwrap().join(relative)
    }

    pub(super) fn build(
        &self,
        agent: Agent,
        mode: Mode,
        var: &dyn Fn(&str) -> Option<OsString>,
    ) -> Result<Policy, String> {
        policy::build(&Inputs {
            agent,
            mode,
            directory: &self.scratch.path().join("home/work"),
            private: &self.scratch.path().join("private"),
            home: &self.scratch.path().join("home"),
            protected: &[self.scratch.path().join("home/.neosian")],
            locks: &[self.scratch.path().join("home/.neosian/.store.lock")],
            var,
        })
    }
}

pub(super) fn unset(_: &str) -> Option<OsString> {
    None
}

fn shown(path: &Path) -> String {
    text(path).unwrap()
}

#[test]
fn the_modes_differ_by_one_allowance_the_validated_directory() {
    let fixture = Fixture::new("sandbox-modes");
    for agent in Agent::ALL {
        let read_only = fixture.build(agent, Mode::ReadOnly, &unset).unwrap();
        let write = fixture.build(agent, Mode::Write, &unset).unwrap();
        let work = Rule::Subpath(shown(&fixture.path("home/work")));
        let mut widened = read_only.allow.clone();
        widened.push(work.clone());
        assert_eq!(write.allow, widened, "{agent:?}");
        assert!(!read_only.allow.contains(&work));
        assert_eq!(write.protect, read_only.protect);
        assert_eq!(write.env, read_only.env);
    }
}

#[test]
fn a_read_only_child_writes_only_its_temp_its_state_and_devices() {
    let fixture = Fixture::new("sandbox-read-only");
    let policy = fixture
        .build(Agent::ClaudeCode, Mode::ReadOnly, &unset)
        .unwrap();
    let private = shown(&fixture.path("private"));
    let projects = shown(&fixture.path("home/.claude/projects"));
    let mut expected: Vec<Rule> = [
        "/dev/null",
        "/dev/zero",
        "/dev/tty",
        "/dev/dtracehelper",
        "/dev/ptmx",
    ]
    .map(|device| Rule::Literal(device.to_string()))
    .to_vec();
    expected.push(Rule::Pattern("^/dev/fd/[0-9]+$".to_string()));
    expected.push(Rule::Pattern("^/dev/ttys[0-9]+$".to_string()));
    expected.push(Rule::Subpath(private.clone()));
    expected.push(Rule::Subpath(projects));
    assert_eq!(policy.allow, expected);
    let marker = Rule::Literal(format!("{private}/{}", super::super::MARKER));
    assert_eq!(policy.protect[0], marker);
    for protected in ["home/.neosian", "home/work/.claude", "home/work/.git/hooks"] {
        assert!(
            policy
                .protect
                .contains(&Rule::Subpath(shown(&fixture.path(protected)))),
            "{protected}"
        );
    }
    for protected in [
        "home/work/.mcp.json",
        "home/work/opencode.json",
        "home/work/.git/config",
    ] {
        assert!(
            policy
                .protect
                .contains(&Rule::Literal(shown(&fixture.path(protected)))),
            "{protected}"
        );
    }
    let memory = format!(
        "^{}/projects/[^/]+/memory(/.*)?$",
        escape(&shown(&fixture.path("home/.claude")))
    );
    assert!(policy.protect.contains(&Rule::Pattern(memory)));
    assert_eq!(
        policy.env,
        [
            ("TMPDIR".to_string(), private.clone()),
            ("CLAUDE_CODE_TMPDIR".to_string(), private)
        ]
    );
    assert_eq!(policy.unset, store::CREDENTIAL_VARS.to_vec());
    // The store's lock takes data writes only, its readers open it so.
    assert_eq!(
        policy.locks,
        [shown(&fixture.path("home/.neosian/.store.lock"))]
    );
    let write = fixture
        .build(Agent::ClaudeCode, Mode::Write, &unset)
        .unwrap();
    assert!(
        write.unset.is_empty(),
        "a write child keeps its environment"
    );
}

#[test]
fn each_harness_gets_only_its_own_state_and_never_a_credential() {
    let fixture = Fixture::new("sandbox-state");
    let codex = fixture.build(Agent::Codex, Mode::ReadOnly, &unset).unwrap();
    let opencode = fixture
        .build(Agent::OpenCode, Mode::ReadOnly, &unset)
        .unwrap();
    let root = fixture.path("home/.codex");
    assert!(
        codex
            .allow
            .contains(&Rule::Subpath(shown(&root.join("sessions"))))
    );
    assert!(
        codex
            .allow
            .contains(&Rule::Literal(shown(&root.join("installation_id"))))
    );
    let sqlite = format!(
        "^{}/[a-z_]+_[0-9]+\\.sqlite(-shm|-wal)?$",
        escape(&shown(&root))
    );
    assert!(codex.allow.contains(&Rule::Pattern(sqlite)));
    let data = fixture.path("home/.local/share/opencode");
    assert!(
        opencode
            .allow
            .contains(&Rule::Literal(shown(&data.join("opencode.db"))))
    );
    for policy in [&codex, &opencode] {
        let profile = policy.profile();
        for never in [
            "auth.json",
            "config.toml",
            ".claude.json",
            "Keychains",
            "/.claude/",
        ] {
            assert!(!profile.contains(never), "{never}");
        }
        assert!(policy.env.iter().all(|(name, _)| name == "TMPDIR"));
    }
}

#[test]
fn a_state_directory_override_must_stay_below_home() {
    let fixture = Fixture::new("sandbox-override");
    let moved = fixture.path("home/elsewhere/codex");
    let named = moved.clone().into_os_string();
    let var = move |name: &str| (name == "CODEX_HOME").then(|| named.clone());
    let policy = fixture.build(Agent::Codex, Mode::ReadOnly, &var).unwrap();
    assert!(
        policy
            .allow
            .contains(&Rule::Subpath(shown(&moved.join("tmp"))))
    );
    let relative = |name: &str| (name == "CODEX_HOME").then(|| OsString::from("rel/codex"));
    let policy = fixture
        .build(Agent::Codex, Mode::ReadOnly, &relative)
        .unwrap();
    assert!(
        policy
            .allow
            .contains(&Rule::Subpath(shown(&fixture.path("home/.codex/tmp"))))
    );
    let outside = fixture.path("private").into_os_string();
    let var = move |name: &str| (name == "XDG_DATA_HOME").then(|| outside.clone());
    let refused = fixture
        .build(Agent::OpenCode, Mode::ReadOnly, &var)
        .unwrap_err();
    assert!(refused.contains("opencode's own state"), "{refused}");
}

#[cfg(unix)]
#[test]
fn a_link_is_followed_to_where_it_leads_or_refused_when_it_escapes() {
    use std::os::unix::fs::symlink;
    let fixture = Fixture::new("sandbox-links");
    // A protected place that is already a link inside the directory guards
    // its target too.
    std::fs::create_dir_all(fixture.path("home/work/inner")).unwrap();
    symlink(
        fixture.path("home/work/inner"),
        fixture.path("home/work/.claude"),
    )
    .unwrap();
    let policy = fixture.build(Agent::Codex, Mode::Write, &unset).unwrap();
    assert!(
        policy
            .protect
            .contains(&Rule::Subpath(shown(&fixture.path("home/work/inner"))))
    );
    // A state name that leads out of the state directory is refused.
    std::fs::create_dir_all(fixture.path("home/.claude")).unwrap();
    symlink(
        fixture.path("home/work"),
        fixture.path("home/.claude/projects"),
    )
    .unwrap();
    let refused = fixture
        .build(Agent::ClaudeCode, Mode::ReadOnly, &unset)
        .unwrap_err();
    assert!(refused.contains("claude-code's own state"), "{refused}");
    // The private directory reached through a link is named canonically.
    symlink(fixture.path("private"), fixture.path("alias")).unwrap();
    let policy = policy::build(&Inputs {
        agent: Agent::OpenCode,
        mode: Mode::ReadOnly,
        directory: &fixture.path("home/work"),
        private: &fixture.path("alias"),
        home: &fixture.path("home"),
        protected: &[],
        locks: &[],
        var: &unset,
    })
    .unwrap();
    assert!(
        policy
            .allow
            .contains(&Rule::Subpath(shown(&fixture.path("private"))))
    );
    // A lock file that is a link leading elsewhere gets no allowance.
    std::fs::write(fixture.path("home/work/x"), "").unwrap();
    symlink(
        fixture.path("home/work/x"),
        fixture.path("home/.neosian/.store.lock"),
    )
    .unwrap();
    let policy = fixture.build(Agent::Codex, Mode::ReadOnly, &unset).unwrap();
    assert!(policy.locks.is_empty());
}

#[test]
fn paths_the_profile_cannot_name_exactly_are_refused() {
    for bad in ["/a\"b", "/a\\b", "/a\nb"] {
        assert!(
            text(Path::new(bad)).unwrap_err().contains("cannot name"),
            "{bad:?}"
        );
    }
    assert_eq!(text(Path::new("/a b/c (1)")).unwrap(), "/a b/c (1)");
    assert_eq!(escape("/a.b/c+(1)"), "/a\\.b/c\\+\\(1\\)");
    let scratch = Scratch::new("sandbox-resolve");
    let base = scratch.path().canonicalize().unwrap();
    assert_eq!(resolve(&base.join("not/yet")), Some(base.join("not/yet")));
    assert_eq!(resolve(&base.join("not/../yet")), None);
    assert_eq!(resolve(Path::new("relative")), None);
}
