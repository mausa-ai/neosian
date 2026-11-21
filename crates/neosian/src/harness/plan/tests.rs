use super::*;

fn plan(directory: &Path) -> WirePlan {
    WirePlan {
        agent: "codex".into(),
        mode: "read-only".into(),
        directory: directory.display().to_string(),
        prompt_bytes: 2,
        ..WirePlan::default()
    }
}

fn words(items: &[&str]) -> Vec<String> {
    items.iter().map(|item| item.to_string()).collect()
}

#[test]
fn a_plan_is_checked_field_by_field() {
    let scratch = crate::testing::Scratch::new("harness-plan");
    let home = scratch.path().canonicalize().unwrap();
    let work = home.join("work");
    std::fs::create_dir(&work).unwrap();
    assert!(check(plan(&work), Some(&home), &[]).is_ok());
    let refused = |edit: &dyn Fn(&mut WirePlan)| {
        let mut wire = plan(&work);
        edit(&mut wire);
        check(wire, Some(&home), &[]).is_err()
    };
    assert!(refused(&|p| p.agent = "bash".into()));
    assert!(refused(&|p| p.mode = "admin".into()));
    assert!(refused(&|p| p.prompt_bytes = 0));
    assert!(refused(&|p| p.model = Some("--bare".into())));
    assert!(refused(&|p| p.resume = Some("--last".into())));
    assert!(refused(&|p| p.new_session = Some("not-a-uuid".into())));
    assert!(refused(&|p| p.parent = Some("a b".into())));
    assert!(refused(&|p| p.nonce = Some("short".into())));
    assert!(refused(&|p| p.store = words(&["--exec", "x"])));
    assert!(refused(&|p| p.store = words(&["--root"])));
    assert!(refused(&|p| p.directory = home.display().to_string()));
    assert!(refused(
        &|p| p.directory = home.parent().unwrap().display().to_string()
    ));
    assert!(refused(&|p| p.directory = "/".into()));
    assert!(refused(&|p| p.directory = "relative".into()));
    assert!(check(plan(&work), None, &[]).is_err());
    assert!(check(plan(&work), Some(Path::new("")), &[]).is_err());
    let listed = [home.clone()];
    assert!(check(plan(&home), Some(&home), &listed).is_ok());
    let unknown: Result<WirePlan, _> = serde_json::from_str(
        r#"{"agent":"codex","mode":"write","directory":"/x","prompt_bytes":1,"command":["sh"]}"#,
    );
    assert!(unknown.is_err());
}

#[test]
fn store_flags_take_only_the_forms_neosian_mcp_accepts() {
    let ok = store(&words(&[
        "--root",
        "/r",
        "--mount",
        "scope=user:me,path=user",
        "--mount",
        "scope=user:me/proj:x,path=project,eo",
    ]))
    .unwrap();
    assert_eq!(ok.0, words(&["--root", "/r"]));
    assert_eq!(ok.1.len(), 2);
    assert_eq!(
        store(&words(&["--scope", "user:me"])).unwrap().1,
        words(&["scope=user:me,path=memories"])
    );
    for bad in [
        vec!["--url", "ftp://x"],
        vec!["--url", "https://a b"],
        vec!["--root", "relative"],
        vec!["--root", "/a", "--url", "https://b"],
        vec!["--root", "/a", "--root", "/b"],
        vec!["--mount", "path=user"],
        vec!["--profile", "Bad Name"],
        vec!["--scope", "no scope"],
        vec!["--schema", "Bad;Schema"],
        vec!["--scope", "user:me", "--mount", "scope=user:me,path=user"],
        vec!["--actor", "mcp:x"],
        vec!["--root", ""],
        // User information would put a credential on a command line (#432).
        vec!["--url", "https://user:secret@example.com"],
        vec!["--url", "https://user@example.com/state"],
        vec!["--url", "http://a:b@127.0.0.1:9"],
        vec!["--url", "https://example.com@evil.example/"],
        vec!["--url", "https://a\\@b.example/"],
    ] {
        assert!(store(&words(&bad)).is_none(), "{bad:?}");
    }
    for good in [
        "https://example.com",
        "http://127.0.0.1:8080/state?x=1",
        "https://example.com/a@b",
        "https://example.com?who=a@b",
    ] {
        assert!(store(&words(&["--url", good])).is_some(), "{good}");
    }
    assert_eq!(
        read_only("scope=user:me,path=user"),
        "scope=user:me,path=user,ro"
    );
    assert_eq!(read_only("scope=s,path=p,eo"), "scope=s,path=p,ro");
    assert_eq!(read_only("scope=s,path=p,ro"), "scope=s,path=p,ro");
}
