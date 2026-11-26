//! The body of `restore_records` as the stores receive it: every field,
//! its default when absent, and each refusal by name. Headers have their
//! own cases beside `Header::from_json`, and the items of the four lists
//! are in `record_restore_items_tests.rs`.

use serde_json::{Value, json};

use super::*;
use crate::conversation::turn_records::{Position, Restored, Staged};

#[path = "record_restore_items_tests.rs"]
mod items;

fn body(value: Value) -> Payload {
    value.as_object().expect("an object").clone()
}

fn decoded(value: Value) -> Result<Restore, Fail> {
    let payload = body(value);
    request(&payload).map(|(_, restore)| restore)
}

fn refused(value: Value) -> String {
    let fail = decoded(value).expect_err("the body is refused");
    assert_eq!((fail.status.as_u16(), fail.code), (400, "value_error"));
    assert_eq!(*fail.details, json!({}));
    fail.message
}

/// A body naming `c` and `r` with the fields of `more`.
fn with(more: Value) -> Value {
    let mut whole = json!({"conversation_id": "c", "restore": "r"});
    for (key, value) in more.as_object().expect("an object") {
        whole[key] = value.clone();
    }
    whole
}

fn header() -> Value {
    json!({
        "turn": 1, "created_at": "2026-10-02T09:15:00Z", "actor": "a",
        "redacted": false, "records": 1, "bytes": 1,
    })
}

fn record() -> Value {
    json!({
        "turn": 1, "seq": 0, "message": 0, "role": "user", "kind": "prompt",
        "content": "text", "chunk": 0, "size": 1, "text": "a",
    })
}

#[test]
fn a_probe_names_two_fields_and_every_default_applies_when_absent() {
    let probe = decoded(with(json!({}))).unwrap();
    assert!(probe.is_probe());
    assert_eq!(probe.restore, "r");
    assert_eq!(
        (probe.projections_from, probe.redactions_from),
        (None, None)
    );
    assert!(!probe.commit && !probe.discard);
    // Empty lists carry no item: still a probe.
    let empty = decoded(with(json!({
        "turns": [], "records": [], "projections_from": 0, "projections": [],
        "redactions_from": 0, "redactions": [], "commit": false, "discard": false,
    })))
    .unwrap();
    assert!(empty.is_probe());
    assert_eq!(
        (empty.projections_from, empty.redactions_from),
        (Some(0), Some(0))
    );
    assert!(decoded(with(json!({"discard": true}))).unwrap().discard);
    assert!(decoded(with(json!({"commit": true}))).unwrap().commit);
}

#[test]
fn every_item_is_decoded_as_sent() {
    let part = decoded(with(json!({
        "turns": [header()],
        "records": [record()],
        "projections_from": 3,
        "projections": [
            {"turn": 2, "kind": "epoch", "text": "t", "span": 2},
            {"turn": 1, "kind": "log", "text": ""},
        ],
        "redactions_from": 1,
        "redactions": [
            {"conversation_id": "c", "turns": [1, 2], "actor": "client:ops",
             "created_at": "2026-10-05T10:00:00.5+02:00"},
            {"conversation_id": "c", "turns": [1], "actor": null,
             "created_at": "2026-10-05T08:00:00Z"},
            {"conversation_id": "c", "turns": [1], "created_at": "2026-10-05T08:00:00Z"},
        ],
        "commit": true,
    })))
    .unwrap();
    assert_eq!(part.turns.len(), 1);
    assert_eq!((part.records[0].0, part.records[0].1.seq), (1, 0));
    assert_eq!(
        (part.projections_from, part.redactions_from),
        (Some(3), Some(1))
    );
    let spans: Vec<u64> = part.projections.iter().map(|entry| entry.span).collect();
    assert_eq!(spans, [2, 1], "span is 1 when absent");
    let [first, second, third] = &part.redactions[..] else {
        panic!("three acts");
    };
    assert_eq!(first.actor.as_deref(), Some("client:ops"));
    assert_eq!(first.created_at.z(), "2026-10-05T08:00:00.500000Z");
    assert_eq!((second.actor.clone(), third.actor.clone()), (None, None));
    assert!(part.commit && !part.is_probe());
    assert!(part.check("c").is_ok());
}

#[test]
fn the_body_is_strict_field_by_field() {
    for (more, why) in [
        (json!({"nonsense": 1}), "unknown parameter: 'nonsense'"),
        (json!({"draft": "d"}), "unknown parameter: 'draft'"),
        (
            json!({"restore": 7}),
            "parameter 'restore' must be a string",
        ),
        (
            json!({"restore": null}),
            "parameter 'restore' must be a string",
        ),
        (json!({"turns": null}), "parameter 'turns' must be a list"),
        (json!({"turns": {}}), "parameter 'turns' must be a list"),
        (
            json!({"records": null}),
            "parameter 'records' must be a list",
        ),
        (
            json!({"commit": null}),
            "parameter 'commit' must be a boolean",
        ),
        (json!({"commit": 1}), "parameter 'commit' must be a boolean"),
        (
            json!({"discard": "yes"}),
            "parameter 'discard' must be a boolean",
        ),
        (
            json!({"discard": null}),
            "parameter 'discard' must be a boolean",
        ),
        (
            json!({"projections_from": null, "projections": []}),
            "parameter 'projections_from' must be a non-negative integer",
        ),
        (
            json!({"projections_from": -1, "projections": []}),
            "parameter 'projections_from' must be a non-negative integer",
        ),
        (
            json!({"redactions_from": 0, "redactions": null}),
            "parameter 'redactions' must be a list",
        ),
        // A list and its place are named together, at any value.
        (
            json!({"projections": []}),
            "projections and projections_from come together",
        ),
        (
            json!({"projections_from": 0}),
            "projections and projections_from come together",
        ),
        (
            json!({"redactions": []}),
            "redactions and redactions_from come together",
        ),
        (
            json!({"redactions_from": 0}),
            "redactions and redactions_from come together",
        ),
    ] {
        assert_eq!(refused(with(more.clone())), why, "{more}");
    }
    // The id's own rule, its refusal repeating at most 128 scalars.
    for id in ["", "Bad", "a b", &"r".repeat(65)] {
        assert_eq!(
            refused(with(json!({"restore": id}))),
            format!("restore id {id:?} must be 1 to 64 of [a-z0-9_-]")
        );
    }
    assert_eq!(
        refused(with(json!({"restore": "R".repeat(4096)}))),
        format!(
            "restore id {:?} must be 1 to 64 of [a-z0-9_-]",
            "R".repeat(128)
        )
    );
    assert!(decoded(with(json!({"restore": "0_-z".repeat(16)}))).is_ok());
    assert_eq!(
        refused(json!({"restore": "r"})),
        "missing parameter: 'conversation_id'"
    );
    assert_eq!(
        refused(json!({"conversation_id": "c"})),
        "missing parameter: 'restore'"
    );
}

#[test]
fn a_discard_names_no_content_and_no_commit_at_any_value() {
    let why = "a discard carries no content and no commit";
    for more in [
        json!({"turns": []}),
        json!({"records": []}),
        json!({"projections_from": 0, "projections": []}),
        json!({"redactions_from": 0, "redactions": []}),
        json!({"commit": false}),
        json!({"commit": true}),
        json!({"turns": [header()]}),
    ] {
        let mut whole = with(more);
        whole["discard"] = json!(true);
        assert_eq!(refused(whole.clone()), why, "{whole}");
        // Named false, a discard forbids nothing.
        whole["discard"] = json!(false);
        assert!(decoded(whole).is_ok());
    }
}

#[test]
fn the_request_names_its_conversation_and_the_answer_keeps_item_12s_order() {
    let payload = body(with(json!({"turns": [header()], "records": [record()]})));
    let (conversation_id, restore) = request(&payload).unwrap();
    assert_eq!((conversation_id, restore.restore.as_str()), ("c", "r"));
    let mut done = Restored::new("r", Staged::Committed, Some(Position::default()));
    (done.stored_bytes, done.freed_bytes) = (7, 9);
    done.digest = Some("ab".into());
    assert_eq!(
        crate::pyfmt::dumps(&done.to_json(), false),
        "{\"restore\":\"r\",\"state\":\"committed\",\"position\":{\"turn\":0,\"seq\":0,\
         \"complete\":true,\"projections\":0,\"redactions\":0},\"stored_bytes\":7,\
         \"freed_bytes\":9,\"digest\":\"ab\"}"
    );
    let none = Restored::new("r", Staged::None, None);
    assert_eq!(
        crate::pyfmt::dumps(&none.to_json(), false),
        "{\"restore\":\"r\",\"state\":\"none\",\"position\":null,\"stored_bytes\":0,\
         \"freed_bytes\":0,\"digest\":null}"
    );
}

#[test]
fn a_record_refusal_repeats_at_most_128_scalars() {
    let records = |item: Value| with(json!({"turns": [header()], "records": [item]}));
    let long = format!("{}{}", "é".repeat(128), "x".repeat(4096));
    let cut = "é".repeat(128);
    let mut unknown = record();
    unknown[&long] = json!(1);
    assert_eq!(
        refused(records(unknown)),
        format!("unknown record field {cut:?}")
    );
    for (key, what) in [
        ("role", "record role"),
        ("kind", "record kind"),
        ("content", "content form"),
    ] {
        let mut item = record();
        item[key] = json!(long);
        assert_eq!(refused(records(item)), format!("unknown {what} {cut:?}"));
    }
}
