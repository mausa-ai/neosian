//! The review's hardening (X4e): state aliases, the lock exception's file,
//! the probe's place and the private directory's cleanup, inside a scratch
//! directory only; and the paths the profile cannot name.

use std::os::unix::fs::symlink;
use std::path::Path;

use super::policy::{Rule, escape, resolve, text};
use super::tests::{Fixture, unset};
use super::*;
use crate::testing::Scratch;

#[test]
fn a_state_name_that_aliases_its_state_directory_is_refused() {
    // P3: `sessions` leading to the Codex home itself would make its
    // configuration and sign-in writable; leading elsewhere inside it too.
    for target in ["home/.codex", "home/.codex/archive"] {
        let fixture = Fixture::new("sandbox-alias");
        std::fs::create_dir_all(fixture.path("home/.codex/archive")).unwrap();
        symlink(fixture.path(target), fixture.path("home/.codex/sessions")).unwrap();
        let refused = fixture.build(Agent::Codex, Mode::ReadOnly, &unset);
        let refused = refused.unwrap_err();
        assert!(refused.contains("codex's own state"), "{target}: {refused}");
    }
}

#[test]
fn the_lock_exception_needs_one_regular_file() {
    // P7: a missing lock or one sharing its file with another gets none.
    let fixture = Fixture::new("sandbox-lock-file");
    let lock = fixture.path("home/.neosian/.store.lock");
    let built = || {
        fixture
            .build(Agent::OpenCode, Mode::ReadOnly, &unset)
            .unwrap()
    };
    assert_eq!(built().locks, [text(&lock).unwrap()]);
    std::fs::hard_link(&lock, fixture.path("home/.neosian/other")).unwrap();
    assert!(built().locks.is_empty(), "a hard-linked lock");
    std::fs::remove_file(fixture.path("home/.neosian/other")).unwrap();
    std::fs::remove_file(&lock).unwrap();
    assert!(built().locks.is_empty(), "a missing lock");
    std::fs::create_dir(&lock).unwrap();
    assert!(built().locks.is_empty(), "a directory");
}

#[test]
fn the_probe_writes_outside_every_allowance_wherever_temp_lies() {
    // P8: a temporary directory inside the approved directory makes the
    // name beside it writable in `write`; the probe goes elsewhere.
    let fixture = Fixture::new("sandbox-probe-place");
    let private = fixture.path("home/work/tmp/private");
    std::fs::create_dir_all(&private).unwrap();
    let policy = policy::build(&Inputs {
        agent: Agent::Codex,
        mode: Mode::Write,
        directory: &fixture.path("home/work"),
        private: &private,
        home: &fixture.path("home"),
        stores: &fixture.stores(),
        var: &unset,
    })
    .unwrap();
    let target = macos::target(&policy, &private, &fixture.path("home")).unwrap();
    assert!(!target.starts_with(fixture.path("home/work")), "{target:?}");
    assert!(target.starts_with(fixture.path("home")));
    let covered = |rule: &Rule| match rule {
        Rule::Literal(path) | Rule::Subpath(path) => target.starts_with(path),
        Rule::Pattern(_) => false,
    };
    assert!(!policy.allow.iter().any(covered));
    #[cfg(target_os = "macos")]
    assert_eq!(
        macos::probe(Path::new(macos::FACILITY), &policy.profile(), &target),
        Ok(())
    );
}

#[test]
fn a_private_directory_whose_marker_failed_is_removed() {
    // P8: a directory without a marker is never swept as stale.
    let fixture = Fixture::new("sandbox-private");
    let dir = fixture.path("unmarked");
    std::fs::create_dir(&dir).unwrap();
    let failed = Private::marked(dir.clone(), |_| Err(std::io::Error::other("full")));
    assert!(failed.is_err() && !dir.exists());
    let made = Private::make().unwrap();
    let path = made.path().to_path_buf();
    assert!(path.join(super::super::MARKER).exists());
    drop(made);
    assert!(!Path::new(&path).exists());
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
