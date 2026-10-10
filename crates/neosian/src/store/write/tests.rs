//! The mutation rulings of DESIGN §8, each on a fresh root: what a write,
//! a delete, a rename and a redaction leave in the sidecar and on disk.

use std::fs;
use std::path::PathBuf;

use serde_json::Value;

use super::*;
use crate::store::tests::refusal;
use crate::testing::Scratch;

const SCOPE: &str = "user:me/proj:demo";
const DIR: &str = "user%3Ame/proj%3Ademo";

fn fresh(name: &str) -> (Scratch, FileStore, Actor) {
    let home = Scratch::new(name);
    let store = FileStore::open(home.path()).unwrap();
    (home, store, Actor::parse("cli:local").unwrap())
}

fn at(home: &Scratch, relative: &str) -> PathBuf {
    home.path().join(DIR).join(relative)
}

/// A path's rows, newest first: version, action, content, redacted.
fn trail(store: &FileStore, path: &str) -> Vec<(u64, &'static str, String, bool)> {
    let rows = store.versions(SCOPE, path, 50).unwrap();
    rows.into_iter()
        .map(|row| (row.version, row.action.as_str(), row.content, row.redacted))
        .collect()
}

fn row(version: u64, action: &'static str, content: &str) -> (u64, &'static str, String, bool) {
    (version, action, content.to_string(), false)
}

#[test]
fn a_write_appends_one_row_then_the_document() {
    let (home, store, actor) = fresh("write");
    assert_eq!(
        store
            .write(SCOPE, "notes/a", "# A\n", &actor, None)
            .unwrap(),
        1
    );
    let rows = store.versions(SCOPE, "notes/a", 50).unwrap();
    assert_eq!(rows[0].actor.as_deref(), Some("cli:local"));
    let stamp = rows[0].created_at.z();
    assert_eq!(
        fs::read_to_string(at(&home, "documents/notes/a.md")).unwrap(),
        format!(
            "---\nneosian_format: 1\nversion: 1\ncreated_at: '{stamp}'\nupdated_at: '{stamp}'\nactor: cli:local\n---\n# A\n"
        )
    );
    // An identical content still bumps, and the creation moment stays.
    assert_eq!(
        store
            .write(SCOPE, "notes/a", "# A\n", &actor, None)
            .unwrap(),
        2
    );
    assert_eq!(
        trail(&store, "notes/a"),
        [row(2, "modified", "# A\n"), row(1, "created", "# A\n")]
    );
    let again = store.read(SCOPE, "notes/a").unwrap().unwrap();
    assert_eq!(
        (again.envelope.version, again.envelope.created_at.z()),
        (2, stamp)
    );
}

#[test]
fn a_delete_consumes_a_version_and_a_recreation_continues_the_count() {
    let (_home, store, actor) = fresh("delete");
    store.write(SCOPE, "a", "one", &actor, None).unwrap();
    assert_eq!(store.delete(SCOPE, "a", &actor).unwrap(), Some(2));
    assert!(store.read(SCOPE, "a").unwrap().is_none());
    assert_eq!(store.delete(SCOPE, "a", &actor).unwrap(), None);
    assert_eq!(store.delete(SCOPE, "never", &actor).unwrap(), None);
    assert_eq!(store.write(SCOPE, "a", "two", &actor, None).unwrap(), 3);
    let rows = [
        row(3, "created", "two"),
        row(2, "deleted", "one"),
        row(1, "created", "one"),
    ];
    assert_eq!(trail(&store, "a"), rows);
}

#[test]
fn an_expected_version_guards_the_write_and_a_lost_race_appends_nothing() {
    let (_home, store, actor) = fresh("expected");
    let conflict = "[memory_conflict] Memory conflict on 'a' in scope 'user:me/proj:demo': ";
    assert_eq!(
        refusal(store.write(SCOPE, "a", "x", &actor, Some(1))),
        format!("{conflict}document_absent")
    );
    store.write(SCOPE, "a", "one", &actor, None).unwrap();
    assert_eq!(
        refusal(store.write(SCOPE, "a", "x", &actor, Some(2))),
        format!("{conflict}version_mismatch")
    );
    assert_eq!(store.write(SCOPE, "a", "two", &actor, Some(1)).unwrap(), 2);
    assert_eq!(trail(&store, "a").len(), 2);
}

#[test]
fn a_planted_document_without_a_sidecar_keeps_its_number_and_its_keys() {
    let (home, store, actor) = fresh("planted");
    let planted = "---\nneosian_format: 1\nversion: 5\ncreated_at: '2026-08-19T10:00:00Z'\nupdated_at: '2026-08-19T10:00:00Z'\nactor: seed\ncustom: kept\ntags:\n- a\n---\nbody";
    home.plant(&format!("{DIR}/documents/a.md"), planted);
    assert_eq!(store.write(SCOPE, "a", "next", &actor, Some(5)).unwrap(), 6);
    let written = fs::read_to_string(at(&home, "documents/a.md")).unwrap();
    assert!(
        written.starts_with(
            "---\nneosian_format: 1\nversion: 6\ncreated_at: '2026-08-19T10:00:00Z'\n"
        )
    );
    assert!(written.ends_with("\nactor: cli:local\ncustom: kept\ntags:\n- a\n---\nnext"));
    // The keys ride a rename and a redaction too.
    assert_eq!(store.rename(SCOPE, "a", "b", &actor).unwrap(), 1);
    assert_eq!(store.redact(SCOPE, Some("b"), &actor).unwrap(), 1);
    let cleared = fs::read_to_string(at(&home, "documents/b.md")).unwrap();
    assert!(
        cleared.ends_with("\nactor: cli:local\nredacted: true\ncustom: kept\ntags:\n- a\n---\n")
    );
}

#[test]
fn a_rename_is_a_delete_at_the_source_and_a_create_at_the_destination() {
    let (_home, store, actor) = fresh("rename");
    store.write(SCOPE, "a", "one", &actor, None).unwrap();
    store.write(SCOPE, "a", "two", &actor, None).unwrap();
    assert_eq!(store.rename(SCOPE, "a", "dir/b", &actor).unwrap(), 1);
    assert!(store.read(SCOPE, "a").unwrap().is_none());
    assert_eq!(store.read(SCOPE, "dir/b").unwrap().unwrap().content, "two");
    assert_eq!(trail(&store, "a")[0], row(3, "deleted", "two"));
    assert_eq!(trail(&store, "dir/b"), [row(1, "created", "two")]);
    // Back again: the destination continues its own history.
    assert_eq!(store.rename(SCOPE, "dir/b", "a", &actor).unwrap(), 4);
    assert_eq!(
        refusal(store.rename(SCOPE, "never", "c", &actor)),
        "[memory_document_not_found] Memory document not found: 'never' in scope \
         'user:me/proj:demo'"
    );
    store.write(SCOPE, "c", "taken", &actor, None).unwrap();
    for occupied in ["c", "a"] {
        assert_eq!(
            refusal(store.rename(SCOPE, "a", occupied, &actor)),
            format!(
                "[memory_conflict] Memory conflict on '{occupied}' in scope \
                 'user:me/proj:demo': destination_exists"
            )
        );
    }
    assert_eq!(trail(&store, "a").len(), 4);
}

#[test]
fn a_redaction_clears_the_content_and_keeps_the_skeleton() {
    let (home, store, actor) = fresh("redact");
    store.write(SCOPE, "a", "secret", &actor, None).unwrap();
    store.write(SCOPE, "a", "more", &actor, None).unwrap();
    let before = store.versions(SCOPE, "a", 50).unwrap();
    assert_eq!(store.redact(SCOPE, Some("a"), &actor).unwrap(), 1);
    let stamps =
        |rows: &[Row]| -> Vec<String> { rows.iter().map(|row| row.created_at.z()).collect() };
    let after = store.versions(SCOPE, "a", 50).unwrap();
    assert_eq!(stamps(&after), stamps(&before));
    assert_eq!(
        trail(&store, "a"),
        [
            (2, "modified", String::new(), true),
            (1, "created", String::new(), true)
        ]
    );
    let (created, updated) = (before[1].created_at.z(), before[0].created_at.z());
    assert_eq!(
        fs::read_to_string(at(&home, "documents/a.md")).unwrap(),
        format!(
            "---\nneosian_format: 1\nversion: 2\ncreated_at: '{created}'\nupdated_at: '{updated}'\nactor: cli:local\nredacted: true\n---\n"
        )
    );
    // Idempotent in effect and in answer; an unknown path matches nothing
    // and leaves no act on the trail.
    assert_eq!(store.redact(SCOPE, Some("a"), &actor).unwrap(), 1);
    assert_eq!(store.redact(SCOPE, Some("never"), &actor).unwrap(), 0);
    let acts = fs::read_to_string(at(&home, "redactions.jsonl")).unwrap();
    let acts: Vec<Value> = acts
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(acts.len(), 2);
    assert_eq!(
        (&acts[0]["actor"], &acts[0]["path"], &acts[0]["count"]),
        (&"cli:local".into(), &"a".into(), &1.into())
    );
    // Redacted is a state, never sticky: a later write is a fresh document
    // over a history that stays cleared.
    assert_eq!(
        store.write(SCOPE, "a", "fresh", &actor, Some(2)).unwrap(),
        3
    );
    let fresh = store.read(SCOPE, "a").unwrap().unwrap();
    assert_eq!(
        (fresh.content.as_str(), fresh.envelope.redacted),
        ("fresh", false)
    );
    assert_eq!(
        trail(&store, "a")[1..],
        [
            (2, "modified", String::new(), true),
            (1, "created", String::new(), true)
        ]
    );
}

#[test]
fn a_scope_wide_redaction_reaches_the_deleted_paths_too() {
    let (home, store, actor) = fresh("redact-all");
    store.write(SCOPE, "a", "live", &actor, None).unwrap();
    store
        .write(SCOPE, "gone", "was here", &actor, None)
        .unwrap();
    store.delete(SCOPE, "gone", &actor).unwrap();
    assert_eq!(store.redact(SCOPE, None, &actor).unwrap(), 2);
    assert_eq!(
        trail(&store, "gone")[0],
        (2, "deleted", String::new(), true)
    );
    assert!(store.read(SCOPE, "a").unwrap().unwrap().envelope.redacted);
    let act: Value = serde_json::from_str(
        fs::read_to_string(at(&home, "redactions.jsonl"))
            .unwrap()
            .trim(),
    )
    .unwrap();
    assert_eq!((&act["path"], &act["count"]), (&Value::Null, &2.into()));
    assert_eq!(store.redact("user:nobody", None, &actor).unwrap(), 0);
}

#[cfg(unix)]
#[test]
fn everything_lands_private_and_no_temp_file_stays() {
    use std::os::unix::fs::PermissionsExt;
    let (home, store, actor) = fresh("private");
    store.write(SCOPE, "notes/a", "x", &actor, None).unwrap();
    store.redact(SCOPE, Some("notes/a"), &actor).unwrap();
    let mode = |relative: &str| {
        fs::metadata(at(&home, relative))
            .unwrap()
            .permissions()
            .mode()
            & 0o777
    };
    for directory in ["", "documents", "documents/notes", "versions/notes"] {
        assert_eq!(mode(directory), 0o700, "{directory}");
    }
    for file in [
        "documents/notes/a.md",
        "versions/notes/a.jsonl",
        "redactions.jsonl",
    ] {
        assert_eq!(mode(file), 0o600, "{file}");
    }
    for directory in ["documents/notes", "versions/notes"] {
        let names: Vec<_> = fs::read_dir(at(&home, directory))
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .collect();
        assert_eq!(names.len(), 1, "{names:?}");
    }
}
