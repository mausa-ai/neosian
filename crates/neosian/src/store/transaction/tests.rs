use super::*;
use crate::testing::Scratch;

#[test]
fn every_committed_boundary_recovers_once_before_another_operation() {
    for stopped in 0..=6 {
        let scratch = Scratch::new(&format!("redo-boundary-{stopped}"));
        let canonical = scratch.path().canonicalize().unwrap();
        let root = canonical.as_path();
        scratch.plant("user%3Atest/versions/old.jsonl", "previous\n");
        scratch.plant("user%3Atest/documents/old.md", "old body");
        let path = |name: &str| root.join("user%3Atest").join(name);
        let writes = vec![
            WriteOp::append(root, &path("versions/old.jsonl"), "deleted\n".into()).unwrap(),
            WriteOp::put(root, &path("documents/new.md"), "new body".into()).unwrap(),
            WriteOp::remove(root, &path("documents/old.md")).unwrap(),
            WriteOp::put(
                root,
                &path(".changes/events/00000000000000000002.json"),
                "event".into(),
            )
            .unwrap(),
            WriteOp::put(root, &path(".changes/head.json"), "head".into()).unwrap(),
            WriteOp::put(root, &path(".exchange/receipt.json"), "checkpoint".into()).unwrap(),
        ];
        assert!(
            commit_with(root, writes, |n| {
                if n == stopped {
                    Err(io::Error::other("simulated process exit"))
                } else {
                    Ok(())
                }
            })
            .is_err()
        );
        assert!(root.join(DIRECTORY).join("pending.json").exists());
        recover(root).unwrap();
        recover(root).unwrap();
        assert_eq!(
            fs::read_to_string(path("versions/old.jsonl")).unwrap(),
            "previous\ndeleted\n"
        );
        assert_eq!(
            fs::read_to_string(path("documents/new.md")).unwrap(),
            "new body"
        );
        assert!(!path("documents/old.md").exists());
        assert_eq!(
            fs::read_to_string(path(".changes/head.json")).unwrap(),
            "head"
        );
        assert_eq!(
            fs::read_to_string(path(".exchange/receipt.json")).unwrap(),
            "checkpoint"
        );
        assert_eq!(
            fs::read_to_string(path(".changes/events/00000000000000000002.json")).unwrap(),
            "event"
        );
        assert!(!root.join(DIRECTORY).join("pending.json").exists());
    }
}

#[test]
fn recovery_replaces_partial_appends_and_removes_temporary_copies() {
    let scratch = Scratch::new("redo-partial-append");
    let canonical = scratch.path().canonicalize().unwrap();
    let root = canonical.as_path();
    let path = root.join("user%3Atest/versions/a.jsonl");
    scratch.plant("user%3Atest/versions/a.jsonl", "before\n");
    let write = WriteOp::append(root, &path, "complete\n".into()).unwrap();
    assert!(commit_with(root, vec![write], |_| Err(io::Error::other("stop"))).is_err());
    fs::write(&path, "before\ncom").unwrap();
    scratch.plant("user%3Atest/versions/.neosian-tmp-orphan", "PRIVATE_COPY");
    scratch.plant(
        ".transactions/.neosian-stage-11111111-2222-4333-8444-555555555555/body",
        "PRIVATE_COPY",
    );
    recover(root).unwrap();
    assert!(
        !root
            .join(".transactions/.neosian-stage-11111111-2222-4333-8444-555555555555")
            .exists()
    );
    assert_eq!(fs::read_to_string(path).unwrap(), "before\ncomplete\n");
    assert!(
        !root
            .join("user%3Atest/versions/.neosian-tmp-orphan")
            .exists()
    );
}

#[test]
fn corrupt_or_unsafe_recovery_refuses_before_changing_files() {
    let scratch = Scratch::new("redo-invalid");
    let canonical = scratch.path().canonicalize().unwrap();
    let root = canonical.as_path();
    scratch.plant("user%3Atest/documents/a.md", "preserved");
    for plan in [
        r#"{"format":2,"writes":[]}"#,
        r#"{"format":1,"writes":[{"path":"../foreign","operation":"remove"}]}"#,
        r#"{"format":1,"writes":[{"path":"config.toml","operation":"remove"}]}"#,
        r#"{"format":1,"writes":[{"path":"user%3Atest/documents/a.md","operation":"append","offset":9,"prefix_sha256":"wrong","text":"lost"}]}"#,
    ] {
        scratch.plant(".transactions/pending.json", plan);
        assert!(recover(root).is_err());
        assert_eq!(
            fs::read_to_string(root.join("user%3Atest/documents/a.md")).unwrap(),
            "preserved"
        );
    }
}

#[test]
fn streaming_recovery_validates_the_entire_document_before_applying() {
    let scratch = Scratch::new("redo-invalid-late");
    let root = scratch.path().canonicalize().unwrap();
    let write = r#"{"path":"user%3Atest/documents/a.md","operation":"put","text":"lost"}"#;
    for plan in [
        format!(r#"{{"writes":[{write}],"format":2}}"#),
        format!(r#"{{"format":1,"writes":[{write}],"extra":true}}"#),
        format!(r#"{{"format":1,"writes":[{write},{write}]}}"#),
        format!(r#"{{"format":1,"writes":[{write}]}} trailing"#),
        format!(r#"{{"format":1,"writes":[{write}],"format":1}}"#),
    ] {
        scratch.plant("user%3Atest/documents/a.md", "preserved");
        scratch.plant(".transactions/pending.json", &plan);
        assert!(recover(&root).is_err());
        assert_eq!(
            fs::read_to_string(root.join("user%3Atest/documents/a.md")).unwrap(),
            "preserved"
        );
    }
}

#[test]
fn streamed_budget_or_input_failure_never_publishes_a_partial_plan() {
    let scratch = Scratch::new("redo-budget");
    let root = scratch.path().canonicalize().unwrap();
    let path = root.join("user%3Atest/documents/a.md");
    scratch.plant("user%3Atest/documents/a.md", "preserved");
    let write = || WriteOp::put(&root, &path, "\u{0}".repeat(100)).unwrap();
    // Escaped JSON is larger than its unescaped body.
    assert!(commit_stream(&root, std::iter::once(Ok(write())), 300).is_err());
    assert_eq!(fs::read_to_string(&path).unwrap(), "preserved");
    assert!(!root.join(DIRECTORY).join("pending.json").exists());
    let input = [Ok(write()), Err(io::Error::other("lost staged input"))];
    assert!(commit_stream(&root, input.into_iter(), 4096).is_err());
    assert_eq!(fs::read_to_string(&path).unwrap(), "preserved");
    assert_eq!(fs::read_dir(root.join(DIRECTORY)).unwrap().count(), 0);
}
