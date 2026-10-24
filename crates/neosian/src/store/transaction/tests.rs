use super::*;
use crate::testing::Scratch;

#[test]
fn every_committed_boundary_recovers_once_before_another_operation() {
    for stopped in 0..=5 {
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
    recover(root).unwrap();
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
