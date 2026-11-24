//! Where a fold finds a name (§40.54 item 21, T1b2c4): under the
//! conversations, under the staged restores, or both, each by its exact
//! spelling. A conversation that is only a staged restore is found and
//! discarded on request; what appears after the listing is seen under the
//! lock; a directory replaced since the listing is not taken for the one
//! listed; and a name that only another spelling answers for, on a file
//! system that folds case, settles and touches nothing of that other
//! conversation.

use std::fs;

use serde_json::json;

use super::super::super::fold::{Places, STOP};
use super::super::migrating::{settled, whole};
use super::super::restore_source::{request, send, source, stream};
use super::super::{LIMITS, enabled, messages, part, written};
use super::{fold, fold_with, folded};
use crate::conversation::turn_records::records_of;

const STAGED: Places = Places {
    stored: false,
    staged: true,
};

#[test]
fn a_conversation_that_is_only_a_staged_restore_is_found_and_discarded_on_request() {
    let (home, store) = enabled("records-fold-staged");
    send(&store, request(&stream(&source(1_000))[..4])).unwrap();
    assert_eq!(store.record_places().unwrap().places("c-1"), STAGED);
    let refused = fold_with(&store, "c-1", false, &LIMITS).unwrap_err().text();
    assert!(
        refused.contains("(0 open drafts, 1 staged restores)"),
        "{refused}"
    );
    assert!(home.path().join(".records/restore/c-1").is_dir());
    let done = fold_with(&store, "c-1", true, &LIMITS).unwrap();
    assert_eq!((done.folded, done.drafts, done.restores), (0, 0, 1));
    assert!(!home.path().join(".records/restore/c-1").exists());
    assert!(!home.path().join("conversations/c-1").exists());
    assert!(store.record_places().unwrap().names().is_empty());
}

#[test]
fn what_appears_after_the_listing_is_seen_under_the_lock() {
    // A restore committed after the listing: its turns are folded.
    let (_home, store) = enabled("records-fold-interleaved");
    let items = stream(&source(1_000));
    let last = items.len() - 1;
    send(&store, request(&items[..last])).unwrap();
    let mut listed = store.record_places().unwrap();
    assert_eq!(listed.places("c-1"), STAGED);
    let mut commit = request(&items[last..]);
    commit.commit = true;
    send(&store, commit).unwrap();
    let done = store
        .fold_records("c-1", &mut listed, false, &LIMITS)
        .unwrap();
    assert_eq!(done, folded("c-1", 2, 0, 1));
    assert_eq!(whole(&store, "c-1").len(), 3);

    // A draft and a restore opened after the listing are pending work.
    let (_home, store) = enabled("records-fold-late");
    let open = records_of(&messages(&[json!({"role": "user", "content": "open"})])).unwrap();
    part(&store, "c-1", "d1", open, false).unwrap();
    let mut listed = store.record_places().unwrap();
    send(&store, request(&items[..4])).unwrap();
    let both = store.fold_records("c-1", &mut listed, false, &LIMITS);
    let both = both.unwrap_err().text();
    assert!(
        both.contains("(1 open drafts, 1 staged restores)"),
        "{both}"
    );
    let gone = store
        .fold_records("c-1", &mut listed, true, &LIMITS)
        .unwrap();
    assert_eq!((gone.drafts, gone.restores), (1, 1));
    assert!(!store.record_places().unwrap().places("c-1").staged);
}

#[test]
fn a_staged_restore_replaced_since_the_listing_is_not_the_one_listed() {
    // c-1 was listed; it went, and C-1 was staged in its place.
    let (home, store) = enabled("records-fold-replaced");
    let restores = home.path().join(".records/restore");
    fs::create_dir_all(restores.join("c-1/backup")).unwrap();
    let mut listed = store.record_places().unwrap();
    assert_eq!(listed.places("c-1"), STAGED);
    fs::remove_dir_all(restores.join("c-1")).unwrap();
    fs::create_dir_all(restores.join("C-1/backup")).unwrap();
    fs::write(restores.join("C-1/backup/turns.jsonl"), "another's\n").unwrap();
    let done = store
        .fold_records("c-1", &mut listed, true, &LIMITS)
        .unwrap();
    assert_eq!((done.folded, done.drafts, done.restores), (0, 0, 0));
    assert_eq!(
        fs::read_to_string(restores.join("C-1/backup/turns.jsonl")).unwrap(),
        "another's\n"
    );
    assert_eq!(listed.places("C-1"), STAGED);
    assert_eq!(listed.places("c-1"), Places::default());
}

#[cfg(unix)]
#[test]
fn names_read_while_their_directory_was_fresh_are_read_again_whatever_its_time_says() {
    // The same replacement, within one tick of a coarse clock: the
    // directory's time is the same before and after.
    let (home, store) = enabled("records-fold-same-tick");
    let restores = home.path().join(".records/restore");
    fs::create_dir_all(restores.join("c-1/backup")).unwrap();
    let mut listed = store.record_places().unwrap();
    let tick = fs::metadata(&restores).unwrap().modified().unwrap();
    fs::remove_dir_all(restores.join("c-1")).unwrap();
    fs::create_dir_all(restores.join("C-1/backup")).unwrap();
    fs::File::open(&restores)
        .unwrap()
        .set_modified(tick)
        .unwrap();
    assert_eq!(fs::metadata(&restores).unwrap().modified().unwrap(), tick);
    let done = store
        .fold_records("c-1", &mut listed, true, &LIMITS)
        .unwrap();
    assert_eq!((done.folded, done.drafts, done.restores), (0, 0, 0));
    assert!(
        restores.join("C-1/backup").is_dir(),
        "the replacement is kept"
    );
    assert_eq!(listed.places("C-1"), STAGED);
}

#[test]
fn a_stored_conversations_discard_leaves_another_spellings_sweep_as_it_was_published() {
    // C-1 holds an open draft. c-1 is a staged restore whose sweep was
    // published and not yet carried out. Where case folds, both names
    // lead to one directory of staged restores.
    let (home, store) = enabled("records-fold-other-sweep");
    let open = records_of(&messages(&[json!({"role": "user", "content": "open"})])).unwrap();
    part(&store, "C-1", "d1", open, false).unwrap();
    let lower = home.path().join(".records/restore/c-1");
    fs::create_dir_all(lower.join("backup")).unwrap();
    let intent = "{\"drafts\":[],\"restores\":[\"backup\"]}";
    fs::write(lower.join(".sweeping"), intent).unwrap();

    let done = fold_with(&store, "C-1", true, &LIMITS).unwrap();
    assert_eq!((done.folded, done.drafts, done.restores), (0, 1, 0));
    assert!(
        !home
            .path()
            .join("conversations/C-1/.drafts/d1.state")
            .exists()
    );
    assert!(settled(&store, "C-1"));
    assert_eq!(fs::read_to_string(lower.join(".sweeping")).unwrap(), intent);
    assert!(
        lower.join("backup").is_dir(),
        "the other name's restore waits"
    );
    // That name's own next call carries its sweep out.
    let other = fold_with(&store, "c-1", false, &LIMITS).unwrap();
    assert_eq!((other.drafts, other.restores), (0, 0));
    assert!(!lower.exists());
}

#[test]
fn a_name_only_another_spelling_answers_for_settles_and_touches_nothing_of_it() {
    // C-1 holds a fold stopped after its intent; c-1 is a staged restore
    // alone. Where the file system folds case, c-1's paths lead into C-1.
    let (home, store) = enabled("records-fold-spelling");
    let one = messages(&[json!({"role": "user", "content": "one"})]);
    written(&store, "C-1", "d1", &one);
    STOP.with(|at| at.set(2));
    let stopped = fold_with(&store, "C-1", false, &LIMITS);
    STOP.with(|at| at.set(0));
    assert!(
        stopped
            .unwrap_err()
            .text()
            .contains("stopped after fold step 2")
    );
    let upper = home.path().join("conversations/C-1");
    let log = fs::read(upper.join("turns.jsonl")).unwrap();
    fs::create_dir_all(home.path().join(".records/restore/c-1/backup")).unwrap();
    let listed = store.record_places().unwrap();
    assert_eq!(listed.places("c-1"), STAGED);
    assert!(listed.places("C-1").stored && !listed.places("C-1").staged);

    let refused = fold_with(&store, "c-1", false, &LIMITS).unwrap_err().text();
    assert!(
        refused.contains("(0 open drafts, 1 staged restores)"),
        "{refused}"
    );
    let gone = fold_with(&store, "c-1", true, &LIMITS).unwrap();
    assert_eq!((gone.folded, gone.restores), (0, 1));
    assert!(
        upper.join(".fold-pending").is_dir(),
        "the other fold still waits"
    );
    assert_eq!(fs::read(upper.join("turns.jsonl")).unwrap(), log);
    assert!(!home.path().join(".records/restore/c-1").exists());

    assert_eq!(fold(&store, "C-1"), folded("C-1", 0, 1, 0));
    assert!(settled(&store, "C-1"));
}
