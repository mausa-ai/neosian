//! The inputs a plan is built from: models, sessions, prompts, where a
//! child may work and where its harness is found.

use std::path::Path;

use super::*;

#[test]
fn inputs_are_held_to_their_grammar() {
    for good in [
        "opus",
        "opencode/deepseek-v4.1-flash-free",
        "gpt-5.5@high",
        "a#b+c:d",
    ] {
        assert!(model_ok(good), "{good}");
    }
    for bad in [
        "",
        "a b",
        "x;rm",
        "$(x)",
        "é",
        "-x",
        "--dangerously-skip-permissions",
        &"m".repeat(129),
    ] {
        assert!(!model_ok(bad), "{bad}");
    }
    assert!(session_ok("ses_e0476f6c7ffeNt1Y5fzK9bFTke") && session_ok("01a1fb84-1ba9"));
    for bad in ["", "-x", "--resume", "a b", "a/b", ".x"] {
        assert!(!session_ok(bad), "{bad}");
    }
    assert!(prompt_ok("x") && !prompt_ok("") && !prompt_ok(&"x".repeat(PROMPT_BYTES + 1)));
    assert_eq!(which("sh", None), None);
    assert_eq!(Agent::parse("claude-code"), Some(Agent::ClaudeCode));
    assert_eq!(Agent::parse("claude"), None);
    assert_eq!(Agent::NAMES, Agent::ALL.map(Agent::name));
    assert_eq!(Mode::NAMES, [Mode::ReadOnly, Mode::Write].map(Mode::name));
}

#[test]
fn a_child_works_strictly_below_home_or_in_a_listed_directory() {
    let scratch = crate::testing::Scratch::new("harness-place");
    let root = scratch.path().canonicalize().unwrap();
    for name in ["work", "listed", "other"] {
        std::fs::create_dir(root.join(name)).unwrap();
    }
    let (work, listed, other) = (root.join("work"), root.join("listed"), root.join("other"));
    let list = [listed.clone()];
    let home = Some(root.as_path());
    assert_eq!(place(None, &work, home, &list), Ok(work.clone()));
    assert_eq!(place(Some("."), &work, home, &list), Ok(work.clone()));
    assert_eq!(
        place(Some("../listed"), &work, home, &list),
        Ok(listed.clone())
    );
    assert_eq!(
        place(Some("../other"), &work, home, &list),
        Err(Placed::NotAllowed(other.clone()))
    );
    assert_eq!(
        place(Some("missing"), &work, home, &list),
        Err(Placed::Missing)
    );
    assert_eq!(place(None, &root, home, &list), Err(Placed::NeedListed));
    assert_eq!(
        place(Some("."), &root, home, &list),
        Err(Placed::NotAllowed(root.clone()))
    );
    assert_eq!(
        place(Some("listed"), &root, home, &list),
        Ok(listed.clone())
    );
    // An ancestor of home, an empty home and no home all need a listing.
    let ancestor = root.parent().unwrap();
    assert_eq!(place(None, ancestor, home, &list), Err(Placed::NeedListed));
    assert_eq!(
        place(None, &work, Some(Path::new("")), &list),
        Err(Placed::NeedListed)
    );
    assert_eq!(place(None, &work, None, &list), Err(Placed::NeedListed));
    assert_eq!(
        place(Some("../listed"), &work, None, &list),
        Ok(listed.clone())
    );
    let top = root.ancestors().last().unwrap();
    assert_eq!(place(None, top, None, &[]), Err(Placed::NeedListed));
    assert!(bare_key("sentry-1_x") && !bare_key("we.ird") && !bare_key(""));
}

#[test]
fn only_absolute_path_entries_are_searched() {
    let scratch = crate::testing::Scratch::new("harness-which");
    let bin = scratch.path().canonicalize().unwrap().join("bin");
    std::fs::create_dir(&bin).unwrap();
    let program = bin.join(if cfg!(windows) {
        "claude.exe"
    } else {
        "claude"
    });
    std::fs::write(&program, "").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    let joined = std::env::join_paths([Path::new("bin"), bin.as_path()]).unwrap();
    assert_eq!(which("claude", Some(&joined)), Some(program));
    let relative = std::env::join_paths([Path::new("bin")]).unwrap();
    assert_eq!(which("claude", Some(&relative)), None);
}
