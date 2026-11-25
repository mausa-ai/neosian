//! The parent charge follows retained conversation state, including an empty archive's receipt.
use super::restore_source::stamp;
use super::{LIMITS, enabled, messages};
use crate::conversation::Redaction;
use crate::conversation::turn_records::{Restore, Sizes, Write, records_of};
use crate::store::FileStore;
use serde_json::json;

const ID: &str = "quota-existing";

fn empty(store: &FileStore, name: &str) -> (u64, u64) {
    let part = Restore {
        restore: name.into(),
        commit: true,
        ..Restore::default()
    };
    let reply = store.restore_records(ID, part, &LIMITS).unwrap();
    (reply.stored_bytes, reply.freed_bytes)
}

#[test]
fn successive_restores_charge_one_parent_across_reopens_and_erasure() {
    let (home, store) = enabled("single-restore-parent");
    let seed = Sizes::new(ID, "seed");
    assert_eq!(
        empty(&store, "seed"),
        (
            seed.bookkeeping() + seed.committed_marker() + seed.conversation_row(),
            seed.bookkeeping()
        )
    );
    let store = FileStore::open(home.path()).unwrap();
    let next = Sizes::new(ID, "next");
    let marker = (
        next.bookkeeping() + next.committed_marker(),
        next.bookkeeping(),
    );
    assert_eq!(empty(&store, "next"), marker);
    assert_eq!(empty(&store, "next"), (0, 0));
    assert_eq!(store.redact_turns(ID, None, None, None).unwrap(), 0);
    let store = FileStore::open(home.path()).unwrap();
    assert!(store.conversations().unwrap().is_empty());
    assert!(store.turn_redactions(None, None, 10).unwrap().is_empty());
    assert_eq!(
        empty(&store, "next"),
        marker,
        "erasure removed the receipt but retained the parent"
    );
}

#[test]
fn first_seal_charges_a_parent_only_if_nothing_has_published_it() {
    for restored in [false, true] {
        let (home, store) = enabled("single-seal-parent");
        let records = records_of(&messages(&[json!({"role":"user","content":"word"})])).unwrap();
        let sizes = Sizes::new(ID, "write");
        let open = Write {
            draft: "write".into(),
            records: records.clone(),
            ..Write::default()
        };
        let reply = store.write_records(ID, open, &LIMITS).unwrap();
        assert_eq!(
            reply.stored_bytes,
            sizes.bookkeeping() + sizes.record(&records[0])
        );
        if restored {
            let seed = Sizes::new(ID, "seed");
            assert_eq!(
                empty(&store, "seed").0,
                seed.bookkeeping() + seed.committed_marker() + seed.conversation_row()
            );
        }
        let store = FileStore::open(home.path()).unwrap();
        let seal = || Write {
            draft: "write".into(),
            seal: true,
            ..Write::default()
        };
        let reply = store.write_records(ID, seal(), &LIMITS).unwrap();
        let parent = if restored {
            0
        } else {
            sizes.conversation_row()
        };
        assert_eq!(
            (reply.stored_bytes, reply.freed_bytes),
            (sizes.header(None) + parent, sizes.bookkeeping())
        );
        assert_eq!(
            store
                .write_records(ID, seal(), &LIMITS)
                .unwrap()
                .stored_bytes,
            0
        );
    }
}

#[test]
fn a_legacy_trail_owns_a_parent_and_receipt_erasure_preserves_every_act() {
    let (home, store) = enabled("legacy-trail-parent");
    let act = Redaction {
        conversation_id: ID.into(),
        turns: vec![4],
        actor: Some("cli:archive".into()),
        created_at: stamp("2026-11-21T12:00:00Z"),
    };
    store
        .restore_conversation(ID, &[], &[], std::slice::from_ref(&act))
        .unwrap();
    let sizes = Sizes::new(ID, "first");
    assert_eq!(
        empty(&store, "first"),
        (
            sizes.bookkeeping() + sizes.committed_marker(),
            sizes.bookkeeping()
        )
    );
    assert_eq!(store.redact_turns(ID, None, None, None).unwrap(), 0);
    let store = FileStore::open(home.path()).unwrap();
    assert_eq!(store.turn_redactions(None, None, 10).unwrap(), [act]);
    assert!(store.conversations().unwrap().is_empty());
    assert_eq!(
        empty(&store, "first").0,
        sizes.bookkeeping() + sizes.committed_marker()
    );
}

#[test]
fn erasing_unpublished_work_leaves_the_first_parent_charge_available() {
    let (home, store) = enabled("private-draft-parent");
    let write = Write {
        draft: "private".into(),
        records: records_of(&messages(&[json!({"role":"user","content":"unpublished"})])).unwrap(),
        ..Write::default()
    };
    store.write_records(ID, write, &LIMITS).unwrap();
    assert_eq!(store.redact_turns(ID, None, None, None).unwrap(), 0);
    let store = FileStore::open(home.path()).unwrap();
    let sizes = Sizes::new(ID, "first");
    assert_eq!(
        empty(&store, "first").0,
        sizes.bookkeeping() + sizes.committed_marker() + sizes.conversation_row()
    );
}
