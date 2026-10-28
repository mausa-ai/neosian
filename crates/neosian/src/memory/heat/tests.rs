//! The log under a private directory: one row per reader and path, the
//! windows over distinct readers, folds, scrubs and the switch.

use std::fs;
use std::path::PathBuf;

use super::*;

fn stamp(text: &str) -> Stamp {
    Stamp::parse(text).unwrap()
}

fn sandbox(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("neosian-heat-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    dir
}

const NOW: &str = "2026-10-28T12:00:00Z";
const SCOPE: &str = "user:me/proj:x";

#[test]
fn one_row_per_reader_and_path_and_the_file_is_named_by_scope() {
    let dir = sandbox("rows");
    let heat = Heat::default();
    for _ in 0..3 {
        heat.observe_in(&dir, "a", SCOPE, "notes/one", stamp(NOW));
    }
    heat.observe_in(&dir, "a", SCOPE, "notes/two", stamp(NOW));
    heat.observe_in(&dir, "b", SCOPE, "notes/one", stamp(NOW));
    let file = file(&dir, SCOPE);
    assert_eq!(file.file_name().unwrap(), "user%3Ame%2Fproj%3Ax.jsonl");
    let text = fs::read_to_string(&file).unwrap();
    assert_eq!(text.lines().count(), 3);
    assert!(text.starts_with(r#"{"at":"2026-10-28T12:00:00Z","reader":"a","path":"notes/one"}"#));
    let found = counts_in(&dir, SCOPE, stamp(NOW));
    assert_eq!(found["notes/one"].total, 2);
    assert_eq!(found["notes/two"].total, 1);
    assert_eq!(found["notes/one"].last, Some(stamp(NOW)));
    assert!(counts_in(&dir, "user:other", stamp(NOW)).is_empty());
}

#[test]
fn windows_count_distinct_readers_and_repeated_rows_once() {
    let dir = sandbox("windows");
    let rows = [
        ("old", "2026-08-01T00:00:00Z"),
        ("month", "2026-10-10T00:00:00Z"),
        ("week", "2026-10-25T00:00:00Z"),
        ("week", "2026-10-26T00:00:00Z"),
    ];
    for (reader, at) in rows {
        // A second server with no memory of the first writes the row again.
        Heat::default().observe_in(&dir, reader, SCOPE, "doc", stamp(at));
    }
    let found = counts_in(&dir, SCOPE, stamp(NOW));
    assert_eq!(
        found["doc"],
        Counts {
            week: 1,
            month: 2,
            total: 3,
            last: Some(stamp("2026-10-26T00:00:00Z")),
        }
    );
}

#[test]
fn a_compaction_folds_the_old_rows_and_keeps_the_month_verbatim() {
    let dir = sandbox("compact");
    let heat = Heat::default();
    heat.observe_in(&dir, "a", SCOPE, "doc", stamp("2026-01-01T00:00:00Z"));
    heat.observe_in(&dir, "b", SCOPE, "doc", stamp("2026-02-01T00:00:00Z"));
    heat.observe_in(&dir, "c", SCOPE, "other", stamp("2026-02-01T00:00:00Z"));
    heat.observe_in(&dir, "d", SCOPE, "doc", stamp("2026-10-20T00:00:00Z"));
    let file = file(&dir, SCOPE);
    compact(&file, stamp(NOW)).unwrap();
    let text = fs::read_to_string(&file).unwrap();
    let lines: Vec<&str> = text.lines().collect();
    assert_eq!(
        lines,
        [
            r#"{"path":"doc","readers":2,"last":"2026-02-01T00:00:00Z"}"#,
            r#"{"path":"other","readers":1,"last":"2026-02-01T00:00:00Z"}"#,
            r#"{"at":"2026-10-20T00:00:00Z","reader":"d","path":"doc"}"#,
        ]
    );
    let found = counts_in(&dir, SCOPE, stamp(NOW));
    assert_eq!(
        (found["doc"].week, found["doc"].month, found["doc"].total),
        (0, 1, 3)
    );
    assert_eq!(found["doc"].last, Some(stamp("2026-10-20T00:00:00Z")));
    // A second compaction folds the fold.
    compact(&file, stamp("2027-01-01T00:00:00Z")).unwrap();
    let found = counts_in(&dir, SCOPE, stamp("2027-01-01T00:00:00Z"));
    assert_eq!(found["doc"].total, 3);
    assert_eq!(fs::read_to_string(&file).unwrap().lines().count(), 2);
}

#[test]
fn deletion_redaction_and_renames_move_or_drop_the_rows() {
    let dir = sandbox("scrub");
    let heat = Heat::default();
    heat.observe_in(&dir, "a", SCOPE, "keep", stamp(NOW));
    heat.observe_in(&dir, "a", SCOPE, "gone", stamp(NOW));
    heat.observe_in(&dir, "a", SCOPE, "moved", stamp(NOW));
    forget_in(&dir, SCOPE, "gone").unwrap();
    rename_in(&dir, SCOPE, "moved", "user:other", "arrived").unwrap();
    let here = counts_in(&dir, SCOPE, stamp(NOW));
    assert_eq!(here.keys().collect::<Vec<_>>(), ["keep"]);
    let there = counts_in(&dir, "user:other", stamp(NOW));
    assert_eq!(there["arrived"].total, 1);
    rename_in(&dir, SCOPE, "keep", SCOPE, "kept").unwrap();
    let here = counts_in(&dir, SCOPE, stamp(NOW));
    assert_eq!(here.keys().collect::<Vec<_>>(), ["kept"]);
    // Nothing to scrub is not an error, and a foreign line is skipped.
    forget_in(&dir, "user:nobody", "x").unwrap();
    fs::write(file(&dir, SCOPE), "not json\n{\"path\":\"kept\"}\n").unwrap();
    assert!(counts_in(&dir, SCOPE, stamp(NOW)).is_empty());
}

#[test]
fn co_access_counts_distinct_readers_with_the_document_and_in_all() {
    let rows: Vec<(String, String)> = [
        ("s1", "a"),
        ("s1", "b"),
        ("s2", "a"),
        ("s2", "b"),
        ("s3", "b"),
        ("s1", "b"),
        ("s3", "c"),
    ]
    .map(|(reader, path)| (reader.to_string(), path.to_string()))
    .into();
    let with: HashSet<&str> = ["s1", "s2"].into();
    let counts = related::tally(&rows, &with);
    assert_eq!(counts["a"], (2, 2));
    assert_eq!(counts["b"], (2, 3));
    assert_eq!(counts["c"], (0, 1));
}

#[test]
fn verbatim_rows_carry_their_reader_and_folds_none() {
    let dir = sandbox("reads");
    let heat = Heat::default();
    heat.observe_in(&dir, "a", SCOPE, "old", stamp("2026-01-01T00:00:00Z"));
    heat.observe_in(&dir, "b", SCOPE, "new", stamp("2026-10-27T00:00:00Z"));
    compact(&file(&dir, SCOPE), stamp(NOW)).unwrap();
    assert_eq!(
        reads_in(&dir, SCOPE),
        [("b".to_string(), "new".to_string())]
    );
    assert!(reads_in(&dir, "user:nobody").is_empty());
}
