//! The operator sweep traverses draft-only and restore-only conversations,
//! with bytewise keyset paging and the same accounting on preview and apply.

mod recovery;

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use serde_json::{Value, json};

use super::restore_source::stamp;
use super::writing::store_path;
use super::{LIMITS, enabled, messages, part};
use crate::conversation::turn_records::{Header, Restore, Staged, State, records_of};
use crate::store::FileStore;

const TEXT: &str = "früh";
const RESTORE: &str = "archive";

fn draft(store: &FileStore, id: &str, name: &str) -> PathBuf {
    let records = records_of(&messages(&[json!({"role":"user","content":TEXT})])).unwrap();
    let opened = part(store, id, name, records, false).unwrap();
    assert_eq!(opened.stored_bytes, draft_bytes(id, name));
    store_path(store, id, &format!("{name}.state"))
}

fn draft_bytes(id: &str, name: &str) -> u64 {
    // Bookkeeping plus one text record (§40.54 item 14).
    (1024 + 256 + 4 * id.len() + name.len() + TEXT.len()) as u64
}

fn restore_bytes(id: &str) -> u64 {
    // A restore adds the turn header with the source actor.
    draft_bytes(id, RESTORE) + (512 + 4 * id.len() + RESTORE.len() + "cli:t".len()) as u64
}

fn restore(store: &FileStore, id: &str) -> PathBuf {
    let records = records_of(&messages(&[json!({"role":"user","content":TEXT})])).unwrap();
    let header = Header::of(
        1,
        stamp("2025-01-01T00:00:00Z"),
        Some("cli:t".into()),
        &records,
    );
    let request = Restore {
        restore: RESTORE.into(),
        turns: vec![header],
        records: records.into_iter().map(|r| (1, r)).collect(),
        ..Restore::default()
    };
    let opened = store.restore_records(id, request, &LIMITS).unwrap();
    assert_eq!(opened.stored_bytes, restore_bytes(id));
    store.staging(id, RESTORE).unwrap().state
}

fn commit() -> Restore {
    Restore {
        restore: RESTORE.into(),
        commit: true,
        ..Restore::default()
    }
}

fn age(path: &Path) {
    let mut state: Value = serde_json::from_slice(&fs::read(path).unwrap()).unwrap();
    state["touched_at"] = json!("2000-01-01T00:00:00Z");
    fs::write(path, serde_json::to_vec(&state).unwrap()).unwrap();
}

fn old_mtime(path: &Path) {
    let old = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000_000_000);
    let file = fs::File::options().write(true).open(path).unwrap();
    file.set_modified(old).unwrap();
}

fn row(id: &str, drafts: usize, restores: usize, bytes: u64) -> Value {
    json!({"conversation_id":id, "drafts":drafts, "restores":restores, "freed_bytes":bytes})
}

#[test]
fn preview_and_discard_page_only_stale_conversations_with_exact_bytes() {
    let (_home, store) = enabled("records-sweep-pages");
    // Create in reverse of the expected byte order, across both directory trees.
    let last_restore = restore(&store, "a-restore");
    let last_payload = last_restore.with_file_name("records").join("1.jsonl");
    let fresh_draft = draft(&store, "H-fresh", "new");
    let fresh_restore = restore(&store, "H-fresh");
    let mixed_restore = restore(&store, "Z-both");
    let first_mixed = draft(&store, "Z-both", "first");
    let second_mixed = draft(&store, "Z-both", "two");
    let mixed_fresh = draft(&store, "Z-both", "keep");
    let first_draft = draft(&store, "A-draft", "one");
    let stale = [
        &last_restore,
        &mixed_restore,
        &first_mixed,
        &second_mixed,
        &first_draft,
    ];
    for path in stale {
        age(path);
    }

    let fresh = [&fresh_draft, &fresh_restore, &mixed_fresh];
    let fresh_before = fresh.map(|p| fs::read(p).unwrap());
    let stale_before = stale.map(|p| fs::read(p).unwrap());
    let first_two = json!([
        row("A-draft", 1, 0, draft_bytes("A-draft", "one")),
        row(
            "Z-both",
            2,
            1,
            draft_bytes("Z-both", "first") + draft_bytes("Z-both", "two") + restore_bytes("Z-both")
        ),
    ]);
    let last = json!([row("a-restore", 0, 1, restore_bytes("a-restore"))]);
    let preview = store.sweep_records(2, None, false).unwrap();
    assert!(!preview.write);
    assert_eq!(json!(preview.conversations), first_two);
    let cursor = preview.next_cursor.unwrap();
    let end = store.sweep_records(2, Some(&cursor), false).unwrap();
    assert!(end.next_cursor.is_none());
    assert_eq!(json!(end.conversations), last);
    assert_eq!(stale.map(|p| fs::read(p).unwrap()), stale_before);

    let removed = store.sweep_records(2, None, true).unwrap();
    assert!(removed.write);
    assert_eq!(json!(removed.conversations), first_two);
    assert_eq!(removed.next_cursor.as_deref(), Some(cursor.as_str()));
    for path in [&first_mixed, &second_mixed, &first_draft] {
        assert!(!path.exists() && !path.with_extension("jsonl").exists());
    }
    assert!(!mixed_restore.parent().unwrap().exists());
    assert!(
        last_restore.exists() && last_payload.exists(),
        "the next page is still staged"
    );
    let end = store.sweep_records(2, Some(&cursor), true).unwrap();
    assert!(end.next_cursor.is_none() && end.write);
    assert_eq!(json!(end.conversations), last);
    assert!(!last_restore.parent().unwrap().exists());
    assert!(!store.conversation_dir("a-restore").unwrap().exists());
    let empty = store.sweep_records(2, None, true).unwrap();
    assert!(empty.conversations.is_empty() && empty.next_cursor.is_none());
    assert_eq!(fresh.map(|p| fs::read(p).unwrap()), fresh_before);
}

#[test]
fn unreadable_states_are_swept_by_file_age_and_recounted() {
    let (_home, store) = enabled("records-sweep-damaged");
    let bad = [draft(&store, "c", "d"), restore(&store, "c")];
    for state in &bad {
        fs::write(state, "{").unwrap();
    }
    let fresh = store.sweep_records(1, None, true).unwrap();
    assert!(fresh.conversations.is_empty());
    for state in &bad {
        old_mtime(state);
    }
    for write in [false, true] {
        let page = store.sweep_records(1, None, write).unwrap();
        assert_eq!(
            json!(page.conversations),
            json!([row("c", 1, 1, draft_bytes("c", "d") + restore_bytes("c"))])
        );
        assert!(page.next_cursor.is_none());
        assert_eq!(bad.each_ref().map(|p| p.exists()), [!write; 2]);
    }
}

#[test]
fn an_old_seal_and_a_committed_restore_keep_their_receipts() {
    let (_home, store) = enabled("records-sweep-closed");
    draft(&store, "c", "d");
    part(&store, "c", "d", vec![], true).unwrap();
    old_mtime(&store_path(&store, "c", "d.sealed"));
    restore(&store, "r");
    let committed = store.restore_records("r", commit(), &LIMITS).unwrap();
    assert_eq!(committed.state, Staged::Committed);
    let stale = draft(&store, "r", "open");
    age(&stale);
    let page = store.sweep_records(100, None, true).unwrap();
    assert_eq!(
        json!(page.conversations),
        json!([row("r", 1, 0, draft_bytes("r", "open"))])
    );
    assert!(page.next_cursor.is_none());
    assert_eq!(
        part(&store, "c", "d", vec![], false).unwrap().state,
        State::Sealed
    );
    let replay = store.restore_records("r", commit(), &LIMITS).unwrap();
    assert_eq!(
        (replay.state, replay.digest),
        (committed.state, committed.digest)
    );
}

#[test]
fn invalid_sweep_requests_fail_before_discarding_anything() {
    let (_home, store) = enabled("records-sweep-shape");
    let state = draft(&store, "stale", "d");
    age(&state);
    let foreign = crate::store::cursor::encode("list", &[json!("closed")]);
    let long = "a".repeat(4097);
    for (limit, cursor) in [
        (0, None),
        (101, None),
        (1, Some("")),
        (1, Some("!")),
        (1, Some(foreign.as_str())),
        (1, Some(long.as_str())),
    ] {
        assert!(store.sweep_records(limit, cursor, true).is_err());
        assert!(state.exists());
    }
    let page = store.sweep_records(1, None, true).unwrap();
    assert_eq!(
        json!(page.conversations),
        json!([row("stale", 1, 0, draft_bytes("stale", "d"))])
    );
    assert!(!state.exists());
}

#[test]
fn sweeping_a_large_draft_never_loads_the_draft_body() {
    let (_home, store) = enabled("records-sweep-large");
    let body = "x".repeat(4 << 20);
    let records = records_of(&messages(&[json!({"role":"user","content":body})])).unwrap();
    let mut bytes = 0;
    for chunk in super::parts(&records, 983_040) {
        bytes += part(&store, "c", "d", chunk, false).unwrap().stored_bytes;
    }
    age(&store_path(&store, "c", "d.state"));
    for write in [false, true] {
        let (page, peak) = super::memory::peak(|| store.sweep_records(1, None, write));
        assert_eq!(
            json!(page.unwrap().conversations),
            json!([row("c", 1, 0, bytes)])
        );
        assert!(
            peak < 256 * 1024,
            "sweeping a 4 MiB draft peaked at {peak} bytes"
        );
    }
}

#[test]
fn differently_cased_ids_in_the_two_directory_trees_keep_their_own_items() {
    let (_home, store) = enabled("records-sweep-case");
    let upper = draft(&store, "Mixed", "text");
    let lower = restore(&store, "mixed");
    age(&upper);
    age(&lower);

    let draft_row = row("Mixed", 1, 0, draft_bytes("Mixed", "text"));
    let restore_row = row("mixed", 0, 1, restore_bytes("mixed"));
    let preview = store.sweep_records(2, None, false).unwrap();
    assert_eq!(
        json!(preview.conversations),
        json!([draft_row, restore_row])
    );
    assert!(preview.next_cursor.is_none());

    let first = store.sweep_records(1, None, true).unwrap();
    assert_eq!(json!(first.conversations), json!([draft_row]));
    assert!(!upper.exists() && lower.exists());
    let cursor = first.next_cursor.unwrap();
    let last = store.sweep_records(1, Some(&cursor), true).unwrap();
    assert_eq!(json!(last.conversations), json!([restore_row]));
    assert!(last.next_cursor.is_none());
    assert!(!lower.parent().unwrap().exists());
}
