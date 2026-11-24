//! A fold stopped after each of its steps, the store opened again and an
//! ordinary call made, a read or an erasure (§40.54 item 21, T1b2c4):
//! stopped before the rename that publishes it, nothing changed, the open
//! draft included, and a rerun folds; stopped anywhere after, the next call
//! on the conversation publishes the log, then the receipts, removes the
//! records files and the pending work, before it answers.

use std::fs;

use serde_json::json;

use super::super::super::fold::STOP;
use super::super::migrating::{log, say, settled, whole};
use super::super::{LIMITS, enabled, example, messages, part, written};
use super::{Folded, fold_with, folded};
use crate::conversation::rows_header::parse_row;
use crate::conversation::turn_records::{State, records_of};
use crate::store::FileStore;

#[test]
fn a_fold_stopped_at_any_step_changes_nothing_or_is_finished_by_the_next_call() {
    for step in 1..=7_u8 {
        let (home, store) = enabled(&format!("records-fold-stop-{step}"));
        say(
            &store,
            "c-1",
            &[json!({"role": "user", "content": "first"})],
            None,
        );
        written(&store, "c-1", "d1", &example());
        let last = messages(&[json!({"role": "user", "content": "second"})]);
        written(&store, "c-1", "d2", &last);
        let open = records_of(&last).unwrap();
        part(&store, "c-1", "d3", open, false).unwrap();
        let (original, before) = (fs::read(log(&store, "c-1")).unwrap(), whole(&store, "c-1"));
        STOP.with(|at| at.set(step));
        let stopped = fold_with(&store, "c-1", true, &LIMITS);
        STOP.with(|at| at.set(0));
        let why = stopped.unwrap_err().text();
        assert!(
            why.contains(&format!("stopped after fold step {step}")),
            "{why}"
        );
        // As the stop left it, nothing yet settled: until the log is
        // published every records file is there, and from then on every
        // row is whole, so no header is ever without its records.
        let records = home.path().join("conversations/c-1/records");
        let rows = fs::read_to_string(log(&store, "c-1")).unwrap();
        let all_whole = rows
            .lines()
            .all(|row| parse_row(row, 1, "c-1", false).is_ok());
        assert_eq!(all_whole, step >= 3, "step {step}");
        if step < 3 {
            assert_eq!(fs::read_dir(&records).unwrap().count(), 2, "step {step}");
        }
        drop(store);

        let store = FileStore::open(home.path()).unwrap();
        let erased = step % 2 == 0;
        if erased {
            assert_eq!(
                store.redact_turns("c-1", None, Some(&[2]), None).unwrap(),
                1
            );
        }
        let now = whole(&store, "c-1");
        assert!(settled(&store, "c-1"), "step {step}");
        assert_eq!((&now[0], &now[2]), (&before[0], &before[2]), "step {step}");
        assert_eq!(now[1] == before[1], !erased, "step {step}");
        let directory = home.path().join("conversations/c-1");
        let draft = directory.join(".drafts/d3.state");
        if step == 1 {
            assert!(draft.is_file(), "the open draft is not consumed");
            assert_eq!(fs::read(log(&store, "c-1")).unwrap() == original, !erased);
            let rerun = fold_with(&store, "c-1", true, &LIMITS).unwrap();
            let left = if erased { 1 } else { 2 };
            let expected = Folded {
                drafts: 1,
                ..folded("c-1", left, 1, 2 - left)
            };
            assert_eq!(rerun, expected);
        } else {
            let rerun = fold_with(&store, "c-1", false, &LIMITS).unwrap();
            let whole_rows = if erased { 2 } else { 3 };
            assert_eq!(rerun, folded("c-1", 0, whole_rows, 3 - whole_rows));
        }
        assert!(
            !draft.exists() && !directory.join("records").exists(),
            "step {step}"
        );
        assert_eq!(
            directory.join(".drafts/folded/2").exists(),
            !erased,
            "step {step}"
        );
        assert!(directory.join(".drafts/folded/3").is_file(), "step {step}");
        let repeat = part(&store, "c-1", "d2", records_of(&last).unwrap(), true).unwrap();
        assert_eq!(
            (repeat.state, repeat.turn.unwrap().turn),
            (State::Sealed, 3)
        );
        assert_eq!(whole(&store, "c-1"), now);
        say(
            &store,
            "c-1",
            &[json!({"role": "user", "content": "next"})],
            None,
        );
        assert_eq!(store.last_turn_number("c-1").unwrap(), 4);
    }
}

#[test]
fn fold_staging_a_killed_run_left_behind_is_removed_by_the_next_call() {
    let (home, store) = enabled("records-fold-left");
    written(&store, "c-1", "d1", &example());
    let (original, before) = (fs::read(log(&store, "c-1")).unwrap(), whole(&store, "c-1"));
    let directory = home.path().join("conversations/c-1");
    for name in [".fold-preparing", ".fold-retired"] {
        fs::create_dir_all(directory.join(name).join("receipts")).unwrap();
        fs::write(directory.join(name).join("turns.jsonl"), "half a log").unwrap();
    }
    assert_eq!(whole(&store, "c-1"), before);
    assert!(settled(&store, "c-1"));
    assert_eq!(fs::read(log(&store, "c-1")).unwrap(), original);
    assert_eq!(
        fold_with(&store, "c-1", false, &LIMITS).unwrap(),
        folded("c-1", 1, 0, 0)
    );
}

/// Stop immediately after publication, before the new log or removals are visible.
fn stop_after_intent(store: &FileStore, id: &str, discard: bool) {
    STOP.with(|at| at.set(2));
    let result = fold_with(store, id, discard, &LIMITS);
    STOP.with(|at| at.set(0));
    assert!(
        result
            .unwrap_err()
            .text()
            .contains("stopped after fold step 2")
    );
}

#[test]
fn a_reopened_fold_discards_the_restore_selected_under_its_own_name() {
    use super::super::restore_source::{request, send, source, stream};

    let (home, store) = enabled("records-fold-reopen-name");
    send(&store, request(&stream(&source(1_000))[..4])).unwrap();
    written(&store, "c-1", "d1", &example());
    let staged = home.path().join(".records/restore/c-1");
    let pending = home.path().join("conversations/c-1/.fold-pending");
    stop_after_intent(&store, "c-1", true);
    assert!(staged.is_dir() && pending.is_dir());
    drop(store);

    let store = FileStore::open(home.path()).unwrap();
    // On a case-insensitive filesystem both spellings reach the same journal.
    let alias_exists = home.path().join("conversations/C-1").is_dir();
    let reader = if alias_exists { "C-1" } else { "c-1" };
    assert_eq!(store.last_turn_number(reader).unwrap(), 1);
    assert!(!pending.exists());
    assert!(
        !staged.exists(),
        "the originally selected restore was discarded"
    );
}

#[test]
fn a_published_fold_finishes_when_the_records_marker_has_gone() {
    let (home, store) = enabled("records-fold-reopen-no-marker");
    written(&store, "c-1", "d1", &example());
    let before = whole(&store, "c-1");
    stop_after_intent(&store, "c-1", false);
    drop(store);
    fs::remove_dir_all(home.path().join(".records")).unwrap();

    let store = FileStore::open(home.path()).unwrap();
    assert_eq!(whole(&store, "c-1"), before);
    assert!(settled(&store, "c-1"));
}
