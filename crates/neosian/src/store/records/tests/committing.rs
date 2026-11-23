//! Restore publication, replay and recovery through the ordinary readers.
use std::fs;

use super::enabled;
use super::memory::asked;
use super::restore_source::{ID, Sent, parts, request, send, source, staged, stream};
use crate::conversation::rank::Order;
use crate::conversation::rows::render_turn;
use crate::conversation::turn_records::{
    ReadItem, RecordRead, RecordSearch, Restore, Sizes, Staged, UnitDigest, restore_id,
};
use crate::store::FileStore;

fn commit(items: &[Sent]) -> Restore {
    Restore {
        commit: true,
        ..request(items)
    }
}

fn digest(items: &[Sent]) -> String {
    let mut unit = UnitDigest::default();
    for item in items {
        match item {
            Sent::Header(h) => unit.header(h),
            Sent::Record(t, r) => unit.record(*t, r),
            Sent::Projection(_, p) => unit.projection(p),
            Sent::Act(_, a) => unit.act(a),
        }
    }
    unit.finish()
}

#[test]
fn the_final_part_publishes_verbatim_and_replays_its_receipt() {
    let (_home, store) = enabled("records-commit-final");
    let original = source(200_000);
    let items = stream(&original);
    let last = items.len() - 1;
    send(&store, request(&items[..last])).unwrap();
    let done = send(&store, commit(&items[last..])).unwrap();
    let sizes = Sizes::new(ID, &restore_id(ID));
    assert_eq!(done.state, Staged::Committed);
    assert_eq!(done.digest, Some(digest(&items)));
    let act = &original.acts[0];
    assert_eq!(
        done.stored_bytes,
        sizes.act(act.actor.as_deref(), act.turns.len())
            + sizes.conversation_row()
            + sizes.committed_marker()
    );
    assert_eq!(done.freed_bytes, sizes.bookkeeping());
    assert!(!store.staging(ID, &restore_id(ID)).unwrap().dir.exists());
    for again in [request(&[]), commit(&[]), commit(&items[last..])] {
        let reply = send(&store, again).unwrap();
        assert_eq!(
            (reply.state, reply.digest, reply.position),
            (Staged::Committed, done.digest.clone(), done.position)
        );
        assert_eq!((reply.stored_bytes, reply.freed_bytes), (0, 0));
    }
    for rejected in [
        request(&items[..1]),
        commit(&items[..1]),
        Restore {
            discard: true,
            ..request(&[])
        },
    ] {
        assert!(
            send(&store, rejected)
                .unwrap_err()
                .text()
                .contains("restore_committed")
        );
    }
    assert_eq!(store.conversations().unwrap(), [ID]);
    assert_eq!(
        store.first_turn_stamp(ID).unwrap(),
        Some(original.turns[0].created_at)
    );
    assert_eq!(store.last_turn_number(ID).unwrap(), 3);
    assert_eq!(
        store.read_projections(ID, 0, None).unwrap(),
        original.projections
    );
    assert_eq!(
        store.turn_redactions(None, None, 10).unwrap(),
        original.acts
    );
    let as_lines =
        |rows: &[crate::conversation::Turn]| rows.iter().map(render_turn).collect::<Vec<_>>();
    assert_eq!(
        as_lines(&store.read_turns(ID, 0, None).unwrap()),
        as_lines(&original.turns)
    );
    let expected: Vec<_> = items
        .iter()
        .filter_map(|item| match item {
            Sent::Header(header) => Some(ReadItem::Header(header.clone())),
            Sent::Record(turn, record) => Some(ReadItem::Record {
                turn: *turn,
                record: record.clone(),
                tail: None,
                truncated: false,
            }),
            _ => None,
        })
        .collect();
    let mut read = Vec::new();
    let next = store
        .read_records(
            ID,
            &RecordRead {
                page_bytes: 1 << 20,
                ..RecordRead::default()
            },
            &mut |item| {
                read.push(item);
                Ok(())
            },
        )
        .unwrap();
    assert!(next.is_none());
    assert_eq!(read, expected);
    let search = RecordSearch::new("tests", None, Order::Newest, None, None, 10).unwrap();
    assert!(!store.search_records(&search).unwrap().is_empty());
    assert_eq!(
        store
            .search_turns(&["tests".into()], None, 10)
            .unwrap()
            .len(),
        1
    );
}

#[test]
fn interrupted_moves_settle_before_the_first_reader_or_writer() {
    let original = source(1000);
    let items = stream(&original);
    for cut in 1..=7 {
        let (home, store) = enabled("records-commit-interrupted");
        super::super::commit::STOP.with(|n| n.set(cut));
        let stopped = send(&store, commit(&items));
        super::super::commit::STOP.with(|n| n.set(0));
        assert!(
            stopped
                .unwrap_err()
                .text()
                .contains("stopped after restore step")
        );
        drop(store);
        let store = FileStore::open(home.path()).unwrap();
        match cut {
            1 => assert_eq!(store.conversations().unwrap(), [ID]),
            2 => assert_eq!(store.turn_redactions(None, None, 5).unwrap(), original.acts),
            3 => assert_eq!(
                store.read_projections(ID, 0, None).unwrap(),
                original.projections
            ),
            4 => {
                store.append_projections(ID, &original.projections).unwrap();
                assert_eq!(store.read_projections(ID, 0, None).unwrap().len(), 2);
            }
            5 => assert_eq!(store.last_turn_number(ID).unwrap(), 3),
            6 => assert_eq!(store.read_turns(ID, 0, None).unwrap().len(), 3),
            _ => assert_eq!(send(&store, request(&[])).unwrap().state, Staged::Committed),
        }
        assert_eq!(
            send(&store, commit(&[])).unwrap().digest,
            Some(digest(&items))
        );
        assert!(
            !store
                .conversation_dir(ID)
                .unwrap()
                .join(".committing")
                .exists()
        );
    }
}

#[test]
fn incomplete_references_and_occupied_targets_refuse_before_writing() {
    let items = stream(&source(1000));
    let (_home, store) = enabled("records-commit-refused");
    send(&store, request(&items[..1])).unwrap();
    let before = staged(&store);
    assert!(
        send(&store, commit(&[]))
            .unwrap_err()
            .text()
            .contains("restore_incomplete")
    );
    for invalid in [items.len() - 1, items.len() - 2] {
        let mut bad = items[1..].to_vec();
        match &mut bad[invalid - 1] {
            Sent::Act(_, a) => a.turns.push(80),
            Sent::Projection(_, p) => p.turn = 80,
            _ => unreachable!(),
        }
        assert!(
            send(&store, commit(&bad))
                .unwrap_err()
                .text()
                .contains("restore_incomplete")
        );
        assert_eq!(staged(&store), before);
    }
    store
        .append_projections(ID, &source(1000).projections)
        .unwrap();
    assert!(
        send(&store, commit(&items[1..]))
            .unwrap_err()
            .text()
            .contains("target_occupied")
    );
    assert_eq!(staged(&store), before);
    for invalid in [items.len() - 1, items.len() - 2] {
        let (_home, store) = enabled("records-commit-staged-ref");
        let mut bad = items.clone();
        match &mut bad[invalid] {
            Sent::Act(_, a) => a.turns.push(80),
            Sent::Projection(_, p) => p.turn = 80,
            _ => unreachable!(),
        }
        send(&store, request(&bad)).unwrap();
        assert!(
            send(&store, commit(&[]))
                .unwrap_err()
                .text()
                .contains("restore_incomplete")
        );
    }
}

#[test]
fn publication_allocations_are_independent_of_sealed_record_bytes() {
    for size in [1 << 20, 16 << 20] {
        let (_home, store) = enabled("records-commit-cost");
        let items = stream(&source(size));
        for part in parts(&items, 900_000) {
            send(&store, part).unwrap();
        }
        let staging = store.staging(ID, &restore_id(ID)).unwrap();
        fs::write(
            staging.records.join("9.jsonl"),
            "left by an interrupted part",
        )
        .unwrap();
        let (done, total) = asked(|| send(&store, commit(&[])));
        done.unwrap();
        assert!(total < (1 << 20), "commit of {size} allocated {total}");
        assert!(
            !store
                .conversation_dir(ID)
                .unwrap()
                .join("records/9.jsonl")
                .exists()
        );
    }
}

#[test]
fn a_first_part_without_a_durable_state_publishes_only_its_retry() {
    let (_home, store) = enabled("records-commit-lost-opening");
    let items = stream(&source(1000));
    send(&store, request(&items)).unwrap();
    let staging = store.staging(ID, &restore_id(ID)).unwrap();
    fs::remove_file(staging.state).unwrap();
    let retry = &items[..4];
    assert_eq!(
        send(&store, commit(retry)).unwrap().digest,
        Some(digest(retry))
    );
    assert_eq!(store.read_turns(ID, 0, None).unwrap().len(), 1);
    assert!(
        !store
            .conversation_dir(ID)
            .unwrap()
            .join("records/3.jsonl")
            .exists()
    );
    assert!(store.read_projections(ID, 0, None).unwrap().is_empty());
    assert!(store.turn_redactions(None, None, 10).unwrap().is_empty());
}
