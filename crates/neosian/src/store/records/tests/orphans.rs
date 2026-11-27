//! What a draft change answers is durable, and a first part cut off before
//! its state leaves nothing behind (findings 5 and 6 of the T1 review). A
//! part and a discard sync the drafts directory before they answer, and a
//! restore part syncs its records directory before its checkpoint. A
//! records file left without its state, as a writer stopped between the
//! two leaves it, is removed by a discard, by the sweep once stale and by a
//! fold that discards pending work, with nothing freed.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use serde_json::{Value, json};

use super::super::seal::SYNCED;
use super::writing::store_path;
use super::{LIMITS, enabled, messages, part};
use crate::conversation::turn_records::{Header, Restore, State, Write, records_of};
use crate::store::FileStore;
use crate::store::time::Stamp;

fn prompt() -> Vec<crate::conversation::turn_records::TurnRecord> {
    records_of(&messages(&[json!({"role": "user", "content": "cut off"})])).unwrap()
}

/// Draft `d` of `id` opened by one part, then its state removed, as a
/// writer stopped after its records file and before its state leaves it:
/// the records file.
fn orphan(store: &FileStore, id: &str) -> PathBuf {
    part(store, id, "d", prompt(), false).unwrap();
    fs::remove_file(store_path(store, id, "d.state")).unwrap();
    let records = store_path(store, id, "d.jsonl");
    assert!(records.is_file());
    records
}

fn discard(store: &FileStore, id: &str) -> crate::conversation::turn_records::Written {
    let write = Write {
        draft: "d".into(),
        discard: true,
        ..Write::default()
    };
    store.write_records(id, write, &LIMITS).unwrap()
}

fn synced() -> Vec<PathBuf> {
    SYNCED.with(|synced| std::mem::take(&mut *synced.borrow_mut()))
}

#[test]
fn a_part_and_a_discard_sync_the_drafts_directory_before_they_answer() {
    let (_home, store) = enabled("records-durable-draft");
    let drafts = store.conversation_dir("c-1").unwrap().join(".drafts");
    synced();
    part(&store, "c-1", "d", prompt(), false).unwrap();
    assert!(synced().contains(&drafts), "an opening part");
    let more = records_of(&messages(&[
        json!({"role": "user", "content": "cut off"}),
        json!({"role": "user", "content": "more"}),
    ]))
    .unwrap();
    part(&store, "c-1", "d", more, false).unwrap();
    assert_eq!(synced(), std::slice::from_ref(&drafts), "a continuing part");
    let gone = discard(&store, "c-1");
    assert!(gone.freed_bytes > 0);
    assert_eq!(synced(), [drafts], "a discard");
}

#[test]
fn a_restore_part_syncs_its_records_directory_before_its_checkpoint() {
    let (_home, store) = enabled("records-durable-restore");
    let records = prompt();
    let at = Stamp::parse("2025-01-01T00:00:00Z").unwrap();
    let header = Header::of(1, at, Some("cli:t".into()), &records);
    let request = Restore {
        restore: "archive".into(),
        turns: vec![header],
        records: records.into_iter().map(|record| (1, record)).collect(),
        ..Restore::default()
    };
    synced();
    store.restore_records("c-1", request, &LIMITS).unwrap();
    let staging = store.staging("c-1", "archive").unwrap();
    assert!(synced().contains(&staging.records));
}

#[test]
fn a_records_file_without_its_state_is_discarded_with_nothing_freed() {
    let (_home, store) = enabled("records-orphan-discard");
    let records = orphan(&store, "c-1");
    let gone = discard(&store, "c-1");
    assert_eq!((gone.state, gone.freed_bytes), (State::None, 0));
    assert!(!records.exists());
}

fn old(path: &Path) {
    let old = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000_000_000);
    let file = fs::File::options().write(true).open(path).unwrap();
    file.set_modified(old).unwrap();
}

#[test]
fn the_sweep_removes_a_records_file_without_its_state_once_stale() {
    let (_home, store) = enabled("records-orphan-sweep");
    let fresh = orphan(&store, "c-fresh");
    let stale = orphan(&store, "c-stale");
    old(&stale);
    let row = json!([{"conversation_id": "c-stale", "drafts": 1, "restores": 0, "freed_bytes": 0}]);
    let preview = store.sweep_records(10, None, false).unwrap();
    assert_eq!(json!(preview.conversations), row);
    assert!(stale.exists());
    let swept = store.sweep_records(10, None, true).unwrap();
    assert_eq!(json!(swept.conversations), row);
    assert!(!stale.exists() && fresh.exists());
}

#[test]
fn a_fold_that_discards_pending_work_removes_a_records_file_without_its_state() {
    let (_home, store) = enabled("records-orphan-fold");
    let records = orphan(&store, "c-1");
    let fold =
        |discard| store.fold_records("c-1", &mut store.record_places().unwrap(), discard, &LIMITS);
    let refused = fold(false).unwrap_err().text();
    assert!(refused.contains("1 open drafts"), "{refused}");
    assert!(records.exists());
    let folded: Value = json!(fold(true).unwrap());
    assert_eq!(
        (&folded["drafts"], &folded["restores"]),
        (&json!(1), &json!(0))
    );
    assert!(!records.exists());
}
