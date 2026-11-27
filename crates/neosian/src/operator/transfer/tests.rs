//! The stream of the archive import. Its positions and digest are pinned
//! on item 12's example (`6ef0ac03...`, the digest the design and the
//! contracts compute), and a staged position is accepted only exactly
//! after one item of the stream, sparse turn numbers included. The parts'
//! own tests are in `pack_tests.rs`.

use serde_json::json;

use super::pack::Part;
use super::restore::pass;
use super::stream::walk;
use super::trail::Trail;
use crate::conversation::message::{self, Message};
use crate::conversation::turn_records::Position;
use crate::conversation::{Projection, Redaction, Turn};
use crate::store::FileStore;
use crate::store::time::Stamp;
use crate::testing::Scratch;

pub(super) const ID: &str = "cc-7f3a";
pub(super) const RESTORE: &str = "iecf9f710d465bc0ea0fe2fbba8915596";
const ACTOR: &str = "claude-code:7f3a";
const DIGEST: &str = "6ef0ac035d795df5f9b79c7d7a720b8d40aa4cc5eb90c3a4a3b0d717ddb53b32";

pub(super) fn at(
    turn: u64,
    seq: u64,
    complete: bool,
    projections: u64,
    redactions: u64,
) -> Position {
    Position {
        turn,
        seq,
        complete,
        projections,
        redactions,
    }
}

pub(super) fn stamp(text: &str) -> Stamp {
    Stamp::parse(text).unwrap()
}

pub(super) fn said(role: &str, text: &str) -> Message {
    message::from_json(&json!({"role": role, "content": text})).unwrap()
}

fn turn(number: u64, time: &str, messages: Vec<Message>) -> Turn {
    Turn {
        conversation_id: ID.into(),
        turn: number,
        redacted: messages.is_empty(),
        messages,
        created_at: stamp(time),
        actor: Some(ACTOR.into()),
    }
}

pub(super) fn act(turns: Vec<u64>, time: &str) -> Redaction {
    Redaction {
        conversation_id: ID.into(),
        turns,
        actor: Some("client:default/ops".into()),
        created_at: stamp(time),
    }
}

/// Item 12's example as an archive: turn 1 a system, a user and an
/// assistant message of 32, 500 and 1,000 bytes, turn 2 redacted, turn 3 a
/// prompt of 44 bytes, one epoch projection and one trail act.
fn example(name: &str) -> (Scratch, FileStore) {
    let home = Scratch::new(name);
    let store = FileStore::open(home.path()).unwrap();
    let turns = vec![
        turn(
            1,
            "2026-10-02T09:15:00.000001Z",
            vec![
                said("system", &"x".repeat(32)),
                said("user", &"y".repeat(500)),
                said("assistant", &"z".repeat(1000)),
            ],
        ),
        turn(2, "2026-10-02T09:20:11Z", Vec::new()),
        turn(
            3,
            "2026-10-02T09:31:40Z",
            vec![said("user", &"w".repeat(44))],
        ),
    ];
    let epoch = Projection {
        turn: 3,
        kind: "epoch".into(),
        text: "set up the cache".into(),
        span: 3,
    };
    let acts = [act(vec![2], "2026-10-05T08:00:00Z")];
    store
        .restore_conversation(ID, &turns, &[epoch], &acts)
        .unwrap();
    (home, store)
}

#[test]
fn the_stream_of_the_design_example_has_its_positions_and_digest() {
    let (_home, store) = example("transfer-stream");
    let mut ends = Vec::new();
    let walked = walk(&store, &Trail::File(&store), ID, &mut |_, end| {
        ends.push(end);
        Ok(())
    })
    .unwrap();
    assert_eq!(walked.digest, DIGEST);
    let counts = (walked.turns, walked.projections, walked.redactions);
    assert_eq!(counts, (3, 1, 1));
    assert_eq!(
        ends,
        [
            at(1, 0, false, 0, 0),
            at(1, 1, false, 0, 0),
            at(1, 2, false, 0, 0),
            at(1, 3, true, 0, 0),
            at(2, 0, true, 0, 0),
            at(3, 0, false, 0, 0),
            at(3, 1, true, 0, 0),
            at(3, 1, true, 1, 0),
            at(3, 1, true, 1, 1),
        ]
    );
}

/// The parts of a store's stream when a server holds it up to `from`.
fn parts_from(store: &FileStore, from: Option<Position>) -> Result<Vec<Part>, String> {
    let mut parts = Vec::new();
    let trail = Trail::File(store);
    pass(store, &trail, ID, RESTORE, from, &mut |part| {
        parts.push(part);
        Ok(())
    })
    .map_err(|error| error.text())?;
    Ok(parts)
}

#[test]
fn a_staged_position_counts_only_exactly_after_one_item() {
    let (_home, store) = example("transfer-positions");
    let whole = parts_from(&store, None).unwrap();
    assert_eq!(whole.len(), 1);
    assert!(whole[0].restore.commit && !whole[0].replay);
    // Inside turn 1, at turn 2's skeleton, and with the projection held.
    for (held, repeated) in [
        (at(1, 2, false, 0, 0), 3),
        (at(2, 0, true, 0, 0), 5),
        (at(3, 1, true, 1, 0), 8),
    ] {
        let parts = parts_from(&store, Some(held)).unwrap();
        assert_eq!(parts.len(), 2);
        assert!(parts[0].replay && !parts[0].restore.commit);
        assert_eq!(parts[0].restore.items_count(), repeated);
        assert_eq!(parts[0].end, held);
        assert!(!parts[1].replay && parts[1].restore.commit);
        assert_eq!(parts[1].restore.items_count(), 9 - repeated);
    }
    let all = parts_from(&store, Some(at(3, 1, true, 1, 1))).unwrap();
    assert!(all.len() == 1 && all[0].replay && all[0].restore.commit);
    for elsewhere in [
        at(2, 1, true, 0, 0),  // turn 2 has no records
        at(4, 0, true, 0, 0),  // past the last turn
        at(1, 3, false, 0, 0), // turn 1 is complete there
        at(1, 4, false, 0, 0), // turn 1 has three records
        at(3, 1, true, 2, 0),  // one projection more
        at(3, 1, true, 0, 1),  // an act before the projection
        at(3, 1, true, 1, 2),  // one act more
    ] {
        let refused = parts_from(&store, Some(elsewhere)).err().unwrap();
        assert!(
            refused.contains("not a place in this archive's stream"),
            "{refused}"
        );
    }
}

#[test]
fn sparse_turn_numbers_are_places_only_where_they_exist() {
    let home = Scratch::new("transfer-sparse");
    let store = FileStore::open(home.path()).unwrap();
    let turns = [
        turn(1, "2026-10-02T09:00:01Z", vec![said("user", "one")]),
        turn(3, "2026-10-02T09:00:03Z", vec![said("user", "three")]),
    ];
    store.restore_conversation(ID, &turns, &[], &[]).unwrap();
    let held = parts_from(&store, Some(at(3, 0, false, 0, 0))).unwrap();
    assert_eq!(held.len(), 2);
    for absent in [at(2, 0, true, 0, 0), at(2, 1, true, 0, 0)] {
        assert!(parts_from(&store, Some(absent)).is_err());
    }
}
