//! A restore in parts on a FileStore up to its commit (§40.54 item 12,
//! T1b2b1): a conversation sent in bounded parts staged verbatim, a part
//! sent again changing nothing, an interrupted restore resumed from the
//! probe or discarded with its bytes, a half-restored conversation reading
//! as absent, and item 12's first part to the byte. Memory and refusals are
//! in `restore_refusals.rs`, the source and its stream in `restore_source.rs`.

use std::fs;

use serde_json::json;

use super::restore_source::{
    ID, Sent, Source, parts, request, send, source, staged, stamp, stream,
};
use super::{LIMITS, enabled, messages};
use crate::conversation::Turn;
use crate::conversation::rank::{Order, Request};
use crate::conversation::rows_header::{Row, parse_row};
use crate::conversation::turn_records::{
    Header, Position, Restore, Sizes, Staged, records_of, restore_id,
};
use crate::store::FileStore;

/// The size rule's total for the whole stream (item 14), worked out here.
fn total(source: &Source) -> u64 {
    let sizes = Sizes::new(ID, &restore_id(ID));
    let mut bytes = sizes.bookkeeping();
    for item in stream(source) {
        bytes += match item {
            Sent::Header(h) if h.redacted => sizes.redacted_header(h.actor.as_deref()),
            Sent::Header(h) => sizes.header(h.actor.as_deref()),
            Sent::Record(_, r) => sizes.record(&r),
            Sent::Projection(_, p) => sizes.projection(&p.text),
            Sent::Act(_, a) => sizes.act(a.actor.as_deref(), a.turns.len()),
        };
    }
    bytes
}

#[test]
fn a_conversation_sent_in_parts_is_staged_verbatim() {
    let (_home, store) = enabled("records-restore-verbatim");
    let source = source(200_000);
    let items = stream(&source);
    let requests = parts(&items, 60_000);
    assert!(requests.len() >= 4, "{} parts", requests.len());
    let mut stored = 0;
    for request in requests {
        let answer = send(&store, request).unwrap();
        assert_eq!(answer.state, Staged::Open);
        stored += answer.stored_bytes;
    }
    assert_eq!(stored, total(&source), "the size rule, part by part");
    let records = |turn: &Turn| records_of(&turn.messages).unwrap();
    let probe = send(&store, request(&[])).unwrap();
    let last = records(&source.turns[2]).len() as u64;
    assert_eq!(
        probe.position,
        Some(Position {
            turn: 3,
            seq: last,
            complete: true,
            projections: 1,
            redactions: 1
        })
    );
    let staging = store.staging(ID, &restore_id(ID)).unwrap();
    let log = fs::read_to_string(&staging.turns).unwrap();
    let rows: Vec<Row> = log
        .lines()
        .enumerate()
        .map(|(at, line)| parse_row(line, at + 1, ID, true).unwrap())
        .collect();
    for (row, turn) in rows.iter().zip(&source.turns) {
        match row {
            Row::Records(header) => {
                assert_eq!(
                    (header.turn, header.created_at, &header.actor),
                    (turn.turn, turn.created_at, &turn.actor)
                );
                assert_eq!(header.records, records(turn).len() as u64);
                assert_eq!(header.draft, restore_id(ID));
                let file = staging.records.join(format!("{}.jsonl", turn.turn));
                let lines: Vec<String> = records(turn)
                    .iter()
                    .map(super::super::drafts::line)
                    .collect();
                assert_eq!(fs::read_to_string(file).unwrap(), lines.concat());
            }
            Row::Whole(skeleton) => {
                assert!(skeleton.redacted && turn.redacted && skeleton.messages.is_empty());
                assert_eq!(
                    (skeleton.turn, skeleton.created_at, &skeleton.actor),
                    (turn.turn, turn.created_at, &turn.actor)
                );
                assert!(!staging.records.join("2.jsonl").exists());
            }
        }
    }
    assert_eq!(rows.len(), 3);
    let projections = fs::read_to_string(&staging.projections).unwrap();
    assert_eq!(
        projections,
        crate::conversation::rows::render_projection(&source.projections[0])
    );
    let acts = fs::read_to_string(&staging.redactions).unwrap();
    assert_eq!(
        acts,
        crate::conversation::rows::render_redaction(&source.acts[0])
    );
}

#[test]
fn a_part_sent_again_changes_nothing() {
    let (_home, store) = enabled("records-restore-repeat");
    let items = stream(&source(200_000));
    for request in parts(&items, 60_000) {
        send(&store, request.clone()).unwrap();
        let files = staged(&store);
        let again = send(&store, request).unwrap();
        assert_eq!((again.stored_bytes, again.freed_bytes), (0, 0));
        assert_eq!(staged(&store), files, "a repeat writes nothing");
    }
    let files = staged(&store);
    for request in parts(&items, 23_456) {
        let again = send(&store, request).unwrap();
        assert_eq!((again.stored_bytes, again.freed_bytes), (0, 0));
    }
    assert_eq!(staged(&store), files, "other boundaries, the same stream");
}

/// The items of `items` from a probe's position on, as a client resumes.
fn from_position(items: &[Sent], at: Position) -> Vec<Sent> {
    items
        .iter()
        .filter(|item| match item {
            Sent::Header(h) => h.turn > at.turn,
            Sent::Record(turn, r) => *turn > at.turn || (*turn == at.turn && r.seq >= at.seq),
            Sent::Projection(index, _) => *index >= at.projections,
            Sent::Act(index, _) => *index >= at.redactions,
        })
        .cloned()
        .collect()
}

#[test]
fn an_interrupted_restore_resumes_from_the_probe_or_is_discarded() {
    let (_home, whole) = enabled("records-restore-whole");
    let (_home2, store) = enabled("records-restore-resume");
    let items = stream(&source(200_000));
    for request in parts(&items, 60_000) {
        send(&whole, request).unwrap();
    }
    // Turn 1 whole, then a request killed after its lines and before its
    // state: lines past what the state counts, a later turn's file begun.
    let mut sent = send(&store, request(&items[..4])).unwrap().stored_bytes;
    let staging = store.staging(ID, &restore_id(ID)).unwrap();
    let at = send(&store, request(&[])).unwrap().position.unwrap();
    let mut log = fs::read(&staging.turns).unwrap();
    log.extend_from_slice(b"{\"neosian_format\":2,\"turn\":9");
    fs::write(&staging.turns, log).unwrap();
    let current = staging.records.join(format!("{}.jsonl", at.turn));
    let mut text = fs::read(&current).unwrap();
    text.extend_from_slice(b"{\"half a record");
    fs::write(&current, text).unwrap();
    fs::write(
        staging.records.join("3.jsonl"),
        "left by a killed request\n",
    )
    .unwrap();
    assert_eq!(send(&store, request(&[])).unwrap().position, Some(at));
    let rest = from_position(&items, at);
    for request in parts(&rest, 60_000) {
        sent += send(&store, request).unwrap().stored_bytes;
    }
    let path = |store: &FileStore| store.staging(ID, &restore_id(ID)).unwrap().dir;
    assert_eq!(
        staged(&store),
        staged(&whole),
        "resumed, the same staging byte for byte"
    );
    let discarded = send(
        &store,
        Restore {
            discard: true,
            ..request(&[])
        },
    )
    .unwrap();
    assert_eq!(
        (discarded.state, discarded.freed_bytes),
        (Staged::Discarded, sent)
    );
    assert!(!path(&store).exists());
    let probe = send(&store, request(&[])).unwrap();
    assert_eq!((probe.state, probe.position), (Staged::None, None));
}

#[test]
fn a_half_restored_conversation_reads_as_absent() {
    let (_home, store) = enabled("records-restore-absent");
    let items = stream(&source(200_000));
    for request in parts(&items, 60_000).into_iter().take(2) {
        send(&store, request).unwrap();
    }
    assert!(store.read_turns(ID, 0, None).unwrap().is_empty());
    assert_eq!(store.last_turn_number(ID).unwrap(), 0);
    assert!(store.first_turn_stamp(ID).unwrap().is_none());
    assert!(store.read_projections(ID, 0, None).unwrap().is_empty());
    assert!(store.conversations().unwrap().is_empty());
    let request = Request {
        terms: vec!["tests".into()],
        ids: None,
        limit: 10,
        order: Order::Newest,
        since: None,
        before: None,
    };
    assert!(store.rank_turns(&request).unwrap().is_empty());
    let search = crate::conversation::turn_records::RecordSearch::new(
        "tests",
        None,
        Order::Newest,
        None,
        None,
        10,
    )
    .unwrap();
    assert!(store.search_records(&search).unwrap().is_empty());
    let mut items = 0;
    let read = crate::conversation::turn_records::RecordRead::default();
    store
        .read_records(ID, &read, &mut |_| {
            items += 1;
            Ok(())
        })
        .unwrap();
    assert_eq!(items, 0);
    assert!(!store.conversation_dir(ID).unwrap().exists());
    let appended = store
        .append_turn(
            ID,
            messages(&[json!({"role": "user", "content": "x"})]),
            None,
        )
        .unwrap();
    assert_eq!(
        appended.turn, 1,
        "the empty target takes a turn as any empty one does"
    );
}

/// Item 12's first part, to the byte: turn 1's header and its three
/// records, 4,002 bytes with the restore's bookkeeping.
#[test]
fn item_12s_first_part_answers_as_item_12_says() {
    let (_home, store) = enabled("records-restore-example");
    let id = "cc-7f3a";
    let actor = Some("claude-code:7f3a".to_string());
    let records = records_of(&messages(&[
        json!({"role": "system", "content": "x".repeat(32)}),
        json!({"role": "user", "content": "y".repeat(500)}),
        json!({"role": "assistant", "content": "z".repeat(1_000)}),
    ]))
    .unwrap();
    let header = Header::of(1, stamp("2026-10-02T09:15:00.000001Z"), actor, &records);
    let restore = Restore {
        restore: restore_id(id),
        turns: vec![header],
        records: records.into_iter().map(|r| (1, r)).collect(),
        ..Restore::default()
    };
    let answer = store.restore_records(id, restore, &LIMITS).unwrap();
    assert_eq!(
        answer.to_json().to_string(),
        r#"{"restore":"iecf9f710d465bc0ea0fe2fbba8915596","state":"open","position":{"turn":1,"seq":3,"complete":true,"projections":0,"redactions":0},"stored_bytes":4002,"freed_bytes":0,"digest":null}"#
    );
}
