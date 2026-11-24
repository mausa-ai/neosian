//! A restore in parts as every store takes it (§40.54 items 12 and 22,
//! T1b2b1): the restore id the binary derives, the request's own shape and
//! its items in stream order. The stream's rules are in `restore_stream.rs`.

use serde_json::json;

use super::message;
use crate::conversation::turn_records::{
    Header, Item, Restore, TurnRecord, records_of, restore_id,
};
use crate::conversation::{Projection, Redaction};
use crate::store::time::Stamp;

pub(super) const ID: &str = "cc-7f3a";

fn stamp(text: &str) -> Stamp {
    Stamp::parse(text).unwrap()
}

/// Item 12's example: turn 1 of three records, turn 2 redacted, turn 3 of
/// one record, a projection and a trail act.
pub(super) fn example() -> (Vec<Header>, Vec<(u64, TurnRecord)>, Projection, Redaction) {
    let actor = Some("claude-code:7f3a".to_string());
    let first = records_of(&[
        message(&json!({"role": "system", "content": "x".repeat(32)})),
        message(&json!({"role": "user", "content": "y".repeat(500)})),
        message(&json!({"role": "assistant", "content": "z".repeat(1_000)})),
    ])
    .unwrap();
    let third =
        records_of(&[message(&json!({"role": "user", "content": "w".repeat(44)}))]).unwrap();
    let headers = vec![
        Header::of(
            1,
            stamp("2026-10-02T09:15:00.000001Z"),
            actor.clone(),
            &first,
        ),
        Header {
            turn: 2,
            created_at: stamp("2026-10-02T09:20:11Z"),
            actor: actor.clone(),
            redacted: true,
            records: 0,
            bytes: 0,
        },
        Header::of(3, stamp("2026-10-02T09:31:40Z"), actor, &third),
    ];
    let mut records: Vec<(u64, TurnRecord)> = first.into_iter().map(|r| (1, r)).collect();
    records.extend(third.into_iter().map(|r| (3, r)));
    let entry = Projection {
        turn: 3,
        kind: "epoch".into(),
        text: "set up the cache".into(),
        span: 3,
    };
    let act = Redaction {
        conversation_id: ID.into(),
        turns: vec![2],
        actor: Some("client:default/ops".into()),
        created_at: stamp("2026-10-05T08:00:00Z"),
    };
    (headers, records, entry, act)
}

#[test]
fn the_restore_id_is_item_22s() {
    assert_eq!(restore_id(ID), "iecf9f710d465bc0ea0fe2fbba8915596");
    assert_eq!(restore_id("c-1"), "ia6f7ef47ee8dc84af9056a3051ddc302");
}

#[test]
fn a_request_is_refused_by_its_own_shape_first() {
    let (headers, records, entry, act) = example();
    let base = || Restore {
        restore: restore_id(ID),
        ..Restore::default()
    };
    let refused = |restore: Restore| restore.check(ID).unwrap_err();
    assert!(base().is_probe() && base().check(ID).is_ok());
    let cases: Vec<(Restore, &str)> = vec![
        (
            Restore {
                restore: "Ix".into(),
                ..base()
            },
            "restore id \"Ix\" must be 1 to 64 of [a-z0-9_-]",
        ),
        (
            Restore {
                discard: true,
                turns: headers[..1].to_vec(),
                ..base()
            },
            "a discard carries no content and no commit",
        ),
        (
            Restore {
                discard: true,
                commit: true,
                ..base()
            },
            "a discard carries no content and no commit",
        ),
        (
            Restore {
                turns: vec![headers[2].clone(), headers[0].clone()],
                ..base()
            },
            "a part's turns are ascending",
        ),
        (
            Restore {
                turns: vec![Header {
                    turn: 0,
                    ..headers[0].clone()
                }],
                ..base()
            },
            "turn 0 must be at least 1",
        ),
        (
            Restore {
                turns: vec![Header {
                    redacted: true,
                    ..headers[0].clone()
                }],
                ..base()
            },
            "turn 1: a redacted turn has no records",
        ),
        (
            Restore {
                turns: vec![Header {
                    redacted: false,
                    ..headers[1].clone()
                }],
                ..base()
            },
            "turn 2: an unredacted turn must have at least one record",
        ),
        (
            Restore {
                turns: vec![Header {
                    redacted: false,
                    bytes: 1,
                    ..headers[1].clone()
                }],
                ..base()
            },
            "turn 2: a turn without records holds no bytes",
        ),
        (
            Restore {
                records: vec![records[1].clone(), records[0].clone()],
                ..base()
            },
            "a part's records follow their turns in seq order",
        ),
        (
            Restore {
                projections: vec![entry.clone()],
                ..base()
            },
            "projections need projections_from",
        ),
        (
            Restore {
                projections_from: Some(u64::MAX),
                projections: vec![entry.clone()],
                ..base()
            },
            "projections_from 18446744073709551615 is past the stream's end",
        ),
        (
            Restore {
                projections_from: Some(0),
                projections: vec![Projection {
                    span: 4,
                    ..entry.clone()
                }],
                ..base()
            },
            "projection of turn 3 has invalid fields",
        ),
        (
            Restore {
                redactions_from: Some(0),
                redactions: vec![Redaction {
                    conversation_id: "cc-other".into(),
                    ..act.clone()
                }],
                ..base()
            },
            "a trail act names conversation \"cc-other\", not this one",
        ),
        (
            Restore {
                redactions_from: Some(0),
                redactions: vec![Redaction {
                    turns: vec![],
                    ..act
                }],
                ..base()
            },
            "a trail act has invalid fields",
        ),
        (
            Restore {
                records: vec![records[0].clone(); 2_049],
                ..base()
            },
            "a part carries at most 2048 items",
        ),
    ];
    for (restore, why) in cases {
        assert_eq!(refused(restore), why);
    }
}

#[test]
fn a_parts_items_come_in_stream_order() {
    let (headers, records, entry, act) = example();
    let restore = Restore {
        restore: restore_id(ID),
        turns: headers[1..].to_vec(),
        records: vec![records[1].clone(), records[2].clone(), records[3].clone()],
        projections_from: Some(0),
        projections: vec![entry],
        redactions_from: Some(0),
        redactions: vec![act],
        ..Restore::default()
    };
    restore.check(ID).unwrap();
    let order: Vec<String> = restore
        .items()
        .iter()
        .map(|item| match item {
            Item::Header(h) => format!("h{}", h.turn),
            Item::Record(turn, r) => format!("r{turn}.{}", r.seq),
            Item::Projection(at, _) => format!("p{at}"),
            Item::Act(at, _) => format!("a{at}"),
        })
        .collect();
    assert_eq!(order, ["r1.1", "r1.2", "h2", "h3", "r3.0", "p0", "a0"]);
}
