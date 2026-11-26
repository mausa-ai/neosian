//! Request shapes the examples of wire.md do not show, as raw bytes: an
//! empty list is sent and not dropped, a flag at true is sent, a place is
//! sent beside its empty list. And the answers they meet: the `discarded`
//! states, an echoed order that does not match, a record without its turn.

use super::{asked, unreadable};
use crate::conversation::rank::Order;
use crate::conversation::turn_records::{Position, RecordRead, Restore, Staged, State, Write};
use crate::remote::records::RecordQuery;

const NO_PAGE: &str = r#"{"turns":[],"records":[],"continuation":null}"#;
const NO_HITS: &str = r#"{"hits":[]}"#;

#[test]
fn an_empty_kinds_list_is_sent_on_a_read_and_on_a_search() {
    let read = RecordRead {
        kinds: Some(vec![]),
        ..RecordRead::default()
    };
    let (sent, done) = asked(NO_PAGE, |store| {
        store.read_records("c", &read, &mut |_| Ok(true))
    });
    done.unwrap();
    assert_eq!(sent, r#"{"conversation_id":"c","kinds":[]}"#);
    let query = RecordQuery {
        query: "x".into(),
        conversations: Some(vec![]),
        kinds: Some(vec![]),
        ..RecordQuery::default()
    };
    let (sent, hits) = asked(NO_HITS, |store| store.search_records(&query));
    assert!(hits.unwrap().is_empty());
    assert_eq!(sent, r#"{"query":"x","conversations":[],"kinds":[]}"#);
}

#[test]
fn a_range_and_time_bounds_are_sent_and_newest_is_named() {
    let read = RecordRead {
        after: 2,
        through: Some(5),
        ..RecordRead::default()
    };
    let (sent, done) = asked(NO_PAGE, |store| {
        store.read_records("c", &read, &mut |_| Ok(true))
    });
    done.unwrap();
    assert_eq!(sent, r#"{"conversation_id":"c","after":2,"through":5}"#);
    let query = RecordQuery {
        query: "two words".into(),
        order: Some(Order::Newest),
        since: Some("2026-11-01T00:00:00Z".into()),
        before: Some("2026-12-01T00:00:00Z".into()),
        ..RecordQuery::default()
    };
    let newest = r#"{"hits":[],"order":"newest"}"#;
    let (sent, hits) = asked(newest, |store| store.search_records(&query));
    assert!(hits.unwrap().is_empty());
    assert_eq!(
        sent,
        r#"{"query":"two words","order":"newest","since":"2026-11-01T00:00:00Z","before":"2026-12-01T00:00:00Z"}"#
    );
}

#[test]
fn an_echoed_order_that_is_not_the_requests_is_refused() {
    let named = |order| RecordQuery {
        query: "x".into(),
        order,
        ..RecordQuery::default()
    };
    for (order, answer) in [
        (Some(Order::Relevance), r#"{"hits":[],"order":"newest"}"#),
        (Some(Order::Newest), NO_HITS),
        (None, r#"{"hits":[],"order":"newest"}"#),
        (Some(Order::Newest), r#"{"hits":[],"order":7}"#),
    ] {
        let (_, hits) = asked(answer, |store| store.search_records(&named(order)));
        assert_eq!(unreadable(hits), "order");
    }
}

#[test]
fn a_discard_is_sent_and_its_state_decoded_on_both_routes() {
    let discard = Write {
        draft: "d1".into(),
        discard: true,
        ..Write::default()
    };
    let answer = r#"{"draft":"d1","state":"discarded","next_seq":3,"turn":null,"stored_bytes":0,"freed_bytes":900}"#;
    let (sent, written) = asked(answer, |store| store.write_records("c", &discard));
    let written = written.unwrap();
    assert_eq!(
        sent,
        r#"{"conversation_id":"c","draft":"d1","discard":true,"records":[]}"#
    );
    assert_eq!(
        (written.state, written.next_seq, written.freed_bytes),
        (State::Discarded, 3, 900)
    );
    let discard = Restore {
        restore: "r1".into(),
        discard: true,
        ..Restore::default()
    };
    let answer = r#"{"restore":"r1","state":"discarded","position":{"turn":2,"seq":1,"complete":false,"projections":0,"redactions":0},"stored_bytes":0,"freed_bytes":4002,"digest":null}"#;
    let (sent, restored) = asked(answer, |store| store.restore_records("c", &discard));
    let restored = restored.unwrap();
    assert_eq!(
        sent,
        r#"{"conversation_id":"c","restore":"r1","discard":true}"#
    );
    let position = Position {
        turn: 2,
        seq: 1,
        complete: false,
        projections: 0,
        redactions: 0,
    };
    assert_eq!(
        (restored.state, restored.position, restored.freed_bytes),
        (Staged::Discarded, Some(position), 4_002)
    );
}

#[test]
fn a_place_goes_beside_its_empty_list_and_a_commit_goes_alone() {
    let open = r#"{"restore":"r1","state":"open","position":{"turn":1,"seq":1,"complete":true,"projections":3,"redactions":0},"stored_bytes":0,"freed_bytes":0,"digest":null}"#;
    let placed = Restore {
        restore: "r1".into(),
        projections_from: Some(3),
        redactions_from: Some(0),
        ..Restore::default()
    };
    let (sent, restored) = asked(open, |store| store.restore_records("c", &placed));
    assert_eq!(restored.unwrap().state, Staged::Open);
    assert_eq!(
        sent,
        r#"{"conversation_id":"c","restore":"r1","projections_from":3,"projections":[],"redactions_from":0,"redactions":[]}"#
    );
    let commit = Restore {
        restore: "r1".into(),
        commit: true,
        ..Restore::default()
    };
    let (sent, restored) = asked(open, |store| store.restore_records("c", &commit));
    restored.unwrap();
    assert_eq!(
        sent,
        r#"{"conversation_id":"c","restore":"r1","commit":true}"#
    );
}

#[test]
fn an_answered_record_without_its_turn_is_unreadable() {
    let answer = r#"{"turns":[],"records":[{"seq":0,"message":0,"role":"user","kind":"prompt","content":"text","block":null,"call":null,"call_id":null,"name":null,"chunk":0,"size":1,"text":"x","tail":null,"truncated":false}],"continuation":null}"#;
    let read = RecordRead::default();
    let (_, done) = asked(answer, |store| {
        store.read_records("c", &read, &mut |_| Ok(true))
    });
    assert_eq!(unreadable(done), "turn");
}
