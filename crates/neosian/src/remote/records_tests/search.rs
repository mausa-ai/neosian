//! A record search answer is checked against its request before a hit is
//! trusted (ruling R39), as the ranked history search checks its turns:
//! one test for each check, and one for answers that pass them all.

use serde_json::{Value, json};

use super::{asked, unreadable};
use crate::conversation::rank::Order;
use crate::conversation::turn_records::Kind;
use crate::remote::records::RecordQuery;

/// A hit of conversation `id` at `turn` and `seq`, on day `day` of
/// November 2026.
fn hit(id: &str, turn: u64, seq: u64, day: u64, score: Option<u64>) -> Value {
    let mut hit = json!({
        "conversation_id":id,"turn":turn,"seq":seq,"message":0,"kind":"reply","call_id":null,"name":null,
        "created_at":format!("2026-11-{day:02}T00:00:00Z"),"actor":null,"size":1,
        "excerpt":{"offset":0,"text":"x"},
    });
    if let Some(score) = score {
        hit["score"] = json!(score);
    }
    hit
}

fn query(order: Option<Order>) -> RecordQuery {
    RecordQuery {
        query: "x".into(),
        order,
        ..RecordQuery::default()
    }
}

fn answer(hits: Vec<Value>, order: Option<Order>) -> String {
    let mut answer = json!({ "hits": hits });
    if let Some(order) = order {
        answer["order"] = json!(order.name());
    }
    answer.to_string()
}

/// What the client makes of `hits` answered to `query`.
fn outcome(query: &RecordQuery, hits: Vec<Value>) -> Result<usize, String> {
    let (_, found) = asked(&answer(hits, query.order), |store| {
        store.search_records(query)
    });
    match found {
        Ok(found) => Ok(found.len()),
        refused => Err(unreadable(refused)),
    }
}

#[test]
fn answers_inside_the_request_are_accepted_in_both_orders() {
    // Newest: time, then conversation, then turn descending, `seq` ascending.
    let newest = vec![
        hit("b", 2, 0, 9, None),
        hit("b", 1, 3, 9, None),
        hit("b", 1, 4, 9, None),
        hit("a", 7, 0, 9, None),
        hit("b", 9, 0, 8, None),
    ];
    assert_eq!(outcome(&query(None), newest.clone()), Ok(5));
    assert_eq!(outcome(&query(Some(Order::Newest)), newest), Ok(5));
    // Relevance: the score first, ties in the newest order.
    let ranked = vec![
        hit("a", 1, 0, 2, Some(9)),
        hit("b", 1, 0, 9, Some(4)),
        hit("b", 1, 1, 9, Some(4)),
        hit("a", 1, 5, 2, Some(0)),
    ];
    let bounded = RecordQuery {
        conversations: Some(vec!["a".into(), "b".into()]),
        kinds: Some(vec![Kind::Reply]),
        since: Some("2026-11-02T00:00:00Z".into()),
        before: Some("2026-11-10T00:00:00Z".into()),
        limit: Some(4),
        ..query(Some(Order::Relevance))
    };
    assert_eq!(outcome(&bounded, ranked), Ok(4));
}

#[test]
fn more_hits_than_the_limit_are_refused() {
    let what = Err("more hits than the limit".to_string());
    let one = RecordQuery {
        limit: Some(1),
        ..query(None)
    };
    let two = vec![hit("a", 2, 0, 9, None), hit("a", 1, 0, 9, None)];
    assert_eq!(outcome(&one, two), what);
    // The route's default is 20 when the request names none.
    let many = |count: u64| (0..count).map(|seq| hit("a", 1, seq, 9, None)).collect();
    assert_eq!(outcome(&query(None), many(20)), Ok(20));
    assert_eq!(outcome(&query(None), many(21)), what);
}

#[test]
fn a_hit_outside_the_conversations_asked_is_refused() {
    let only = RecordQuery {
        conversations: Some(vec!["a".into()]),
        ..query(None)
    };
    let found = outcome(
        &only,
        vec![hit("a", 1, 0, 9, None), hit("b", 1, 0, 8, None)],
    );
    assert_eq!(found, Err("a hit outside the conversations asked".into()));
    let none = RecordQuery {
        conversations: Some(vec![]),
        ..query(None)
    };
    assert!(outcome(&none, vec![hit("a", 1, 0, 9, None)]).is_err());
}

#[test]
fn a_hit_outside_the_kinds_asked_is_refused() {
    let what = Err("a hit outside the kinds asked".to_string());
    let prompts = RecordQuery {
        kinds: Some(vec![Kind::Prompt]),
        ..query(None)
    };
    assert_eq!(outcome(&prompts, vec![hit("a", 1, 0, 9, None)]), what);
    // With no kinds named, the five searchable kinds are the selection.
    let mut reasoning = hit("a", 1, 0, 9, None);
    reasoning["kind"] = json!("reasoning");
    assert_eq!(outcome(&query(None), vec![reasoning]), what);
}

#[test]
fn a_hit_outside_the_time_bounds_is_refused() {
    let what = Err("a hit outside the time bounds asked".to_string());
    let bounded = RecordQuery {
        since: Some("2026-11-05T00:00:00Z".into()),
        before: Some("2026-11-09T00:00:00Z".into()),
        ..query(None)
    };
    // `since` is inclusive and `before` exclusive.
    assert_eq!(outcome(&bounded, vec![hit("a", 1, 0, 5, None)]), Ok(1));
    assert_eq!(outcome(&bounded, vec![hit("a", 1, 0, 4, None)]), what);
    assert_eq!(outcome(&bounded, vec![hit("a", 1, 0, 9, None)]), what);
}

#[test]
fn a_score_is_required_under_relevance_and_refused_otherwise() {
    let what = Err("score".to_string());
    let ranked = query(Some(Order::Relevance));
    assert_eq!(outcome(&ranked, vec![hit("a", 1, 0, 9, None)]), what);
    for order in [None, Some(Order::Newest)] {
        assert_eq!(
            outcome(&query(order), vec![hit("a", 1, 0, 9, Some(3))]),
            what
        );
    }
}

#[test]
fn hits_out_of_the_order_asked_are_refused() {
    let what = Err("hits out of order".to_string());
    for (older, newer) in [
        (hit("a", 1, 0, 8, None), hit("a", 1, 0, 9, None)),
        (hit("a", 1, 0, 9, None), hit("b", 1, 0, 9, None)),
        (hit("a", 1, 0, 9, None), hit("a", 2, 0, 9, None)),
        (hit("a", 1, 1, 9, None), hit("a", 1, 0, 9, None)),
        // The same record twice.
        (hit("a", 1, 0, 9, None), hit("a", 1, 0, 9, None)),
    ] {
        assert_eq!(outcome(&query(None), vec![older, newer]), what);
    }
    let ranked = query(Some(Order::Relevance));
    let rising = vec![hit("a", 1, 0, 9, Some(1)), hit("a", 1, 1, 9, Some(2))];
    assert_eq!(outcome(&ranked, rising), what);
    let tied = vec![hit("a", 1, 0, 8, Some(2)), hit("a", 1, 0, 9, Some(2))];
    assert_eq!(outcome(&ranked, tied), what);
}
