//! The record search on a FileStore (§40.54 item 11, T1b2a2): hits in item
//! 11's order naming their records, scores by memory ranking version 1
//! worked out here, `kinds` and time bounds, a tool result named by its
//! call in a second pass, drafts never matching. Scores are in
//! `hit_scores.rs`; selection by `kinds` and time, records and whole turns
//! answering alike, and the bounds in `hit_bounds.rs`.

use serde_json::{Value, json};

use super::{enabled, messages, part, written};
use crate::conversation::Turn;
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{
    ReadItem, RecordHit, RecordRead, RecordSearch, records_of,
};
use crate::store::FileStore;
use crate::store::time::Stamp;
use crate::testing::Scratch;

const T0: &str = "2026-11-21T10:00:00Z";

fn turn(id: &str, number: u64, values: &[Value]) -> Turn {
    Turn {
        conversation_id: id.into(),
        turn: number,
        messages: messages(values),
        created_at: Stamp::parse(T0).unwrap(),
        actor: Some("cli:t".into()),
        redacted: false,
    }
}

/// c-a: turn 1 whole at T0 (a prompt, a call, its result, a reply), turn 2
/// as records, now; c-b: turn 1 whole at T0; an open draft in c-a.
pub(super) fn mixed(name: &str) -> (Scratch, FileStore) {
    let (home, store) = enabled(name);
    let first = turn(
        "c-a",
        1,
        &[
            json!({"role": "user", "content": "find alpha in the logs"}),
            json!({"role": "assistant", "content": null,
                   "tool_calls": [{"id": "t1", "name": "Bash", "arguments": {"cmd": "grep alpha"}}]}),
            json!({"role": "tool", "content": "alpha found twice: alpha", "tool_call_id": "t1"}),
            json!({"role": "assistant", "content": "done"}),
        ],
    );
    store
        .restore_conversation("c-a", &[first], &[], &[])
        .unwrap();
    let other = turn("c-b", 1, &[json!({"role": "user", "content": "alpha"})]);
    store
        .restore_conversation("c-b", &[other], &[], &[])
        .unwrap();
    written(
        &store,
        "c-a",
        "d",
        &messages(&[json!({"role": "user", "content": "alpha again"})]),
    );
    let draft = records_of(&messages(&[
        json!({"role": "user", "content": "alpha draft"}),
    ]))
    .unwrap();
    part(&store, "c-a", "open", draft, false).unwrap();
    (home, store)
}

pub(super) fn search(query: &str, order: Order) -> RecordSearch {
    RecordSearch::new(query, None, order, None, None, 20).unwrap()
}

/// Each hit as (conversation, turn, seq, kind, name, excerpt offset, text).
pub(super) type Seen = (
    String,
    u64,
    u64,
    &'static str,
    Option<String>,
    usize,
    String,
);

pub(super) fn seen(hits: &[RecordHit]) -> Vec<Seen> {
    hits.iter()
        .map(|hit| {
            let (offset, text) = hit.excerpt.clone();
            (
                hit.conversation_id.clone(),
                hit.turn,
                hit.seq,
                hit.kind.as_str(),
                hit.name.clone(),
                offset,
                text,
            )
        })
        .collect()
}

pub(super) fn row(
    id: &str,
    turn: u64,
    seq: u64,
    kind: &'static str,
    name: Option<&str>,
    text: &str,
) -> Seen {
    (
        id.into(),
        turn,
        seq,
        kind,
        name.map(str::to_string),
        0,
        text.into(),
    )
}

#[test]
fn hits_come_in_item_11s_order_naming_their_records() {
    let (_home, store) = mixed("records-hits-order");
    let hits = store
        .search_records(&search("alpha", Order::Newest))
        .unwrap();
    assert_eq!(
        seen(&hits),
        [
            row("c-a", 2, 0, "prompt", None, "alpha again"),
            row("c-b", 1, 0, "prompt", None, "alpha"),
            row("c-a", 1, 0, "prompt", None, "find alpha in the logs"),
            row(
                "c-a",
                1,
                1,
                "tool_call",
                Some("Bash"),
                "{\"cmd\":\"grep alpha\"}"
            ),
            row(
                "c-a",
                1,
                2,
                "tool_result",
                Some("Bash"),
                "alpha found twice: alpha"
            ),
        ]
    );
    let json = hits[4].to_json();
    let keys: Vec<&str> = json
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    assert_eq!(
        keys,
        [
            "conversation_id",
            "turn",
            "seq",
            "message",
            "kind",
            "call_id",
            "name",
            "created_at",
            "actor",
            "size",
            "excerpt"
        ]
    );
    assert_eq!(
        json["excerpt"],
        json!({"offset": 0, "text": "alpha found twice: alpha"})
    );
    assert_eq!(
        (json["created_at"].as_str(), json["actor"].as_str()),
        (Some(T0), Some("cli:t"))
    );
    assert_eq!(
        (
            json["call_id"].as_str(),
            json["message"].as_u64(),
            json["size"].as_u64()
        ),
        (Some("t1"), Some(2), Some(24))
    );
    let read = |id: &str, read: RecordRead| {
        let mut found = Vec::new();
        let mut visit = |item| {
            if let ReadItem::Record { record, .. } = item {
                found.push(record);
            }
            Ok(())
        };
        store.read_records(id, &read, &mut visit).unwrap();
        found
    };
    for hit in &hits {
        let (turn, seq) = (Some(hit.turn), Some(hit.seq));
        let piece = read(
            &hit.conversation_id,
            RecordRead {
                turn,
                seq,
                piece: true,
                ..RecordRead::default()
            },
        );
        let record = piece
            .iter()
            .find(|r| r.seq == hit.seq)
            .expect("the hit's record");
        let at: String = record.text.chars().skip(hit.excerpt.0).collect();
        assert!(
            at.starts_with(&hit.excerpt.1) && record.kind == hit.kind,
            "{hit:?}"
        );
        let around = read(
            &hit.conversation_id,
            RecordRead {
                turn,
                ..RecordRead::default()
            },
        );
        assert!(
            around.iter().any(|r| r.seq == hit.seq),
            "read around {hit:?}"
        );
    }
    let sealed = part(&store, "c-a", "open", Vec::new(), true).unwrap();
    let hits = store
        .search_records(&search("draft", Order::Newest))
        .unwrap();
    assert_eq!(
        seen(&hits),
        [row(
            "c-a",
            sealed.turn.unwrap().turn,
            0,
            "prompt",
            None,
            "alpha draft"
        )]
    );
}
