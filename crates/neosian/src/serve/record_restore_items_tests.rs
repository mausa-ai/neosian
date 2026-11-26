//! The items of a `restore_records` body: headers, records in restore
//! shape, projections and trail acts, each decoded strictly, and the item
//! count checked before any of them.

use serde_json::{Value, json};

use super::super::PART_ITEMS;
use super::{decoded, header, record, refused, with};

#[test]
fn headers_and_records_are_decoded_strictly() {
    let turns = |item: Value| with(json!({"turns": [item]}));
    let mut unknown = header();
    unknown["zeta"] = json!(1);
    assert_eq!(refused(turns(unknown)), "unknown header field \"zeta\"");
    let mut zero = header();
    zero["turn"] = json!(0);
    assert_eq!(
        refused(turns(zero)),
        "header field \"turn\" must be an integer >= 1"
    );
    assert_eq!(refused(turns(json!(1))), "a turn header must be an object");
    let records = |item: Value| with(json!({"turns": [header()], "records": [item]}));
    let mut unplaced = record();
    unplaced.as_object_mut().unwrap().shift_remove("turn");
    assert_eq!(
        refused(records(unplaced)),
        "record field \"turn\" is required"
    );
    let mut unknown = record();
    unknown["tail"] = json!(null);
    assert_eq!(refused(records(unknown)), "unknown record field \"tail\"");
    let mut zero = record();
    zero["turn"] = json!(0);
    assert_eq!(
        refused(records(zero)),
        "record field \"turn\" must be an integer >= 1"
    );
}

#[test]
fn projections_are_decoded_strictly() {
    let entry = |item: Value| with(json!({"projections_from": 0, "projections": [item]}));
    let whole = json!({"turn": 3, "kind": "epoch", "text": "t", "span": 3});
    assert!(decoded(entry(whole.clone())).is_ok());
    let edited = |key: &str, value: Value| {
        let mut item = whole.clone();
        item[key] = value;
        refused(entry(item))
    };
    let dropped = |key: &str| {
        let mut item = whole.clone();
        item.as_object_mut().unwrap().shift_remove(key);
        refused(entry(item))
    };
    assert_eq!(
        edited("extra", json!(1)),
        "unknown parameter: 'projections[0].extra'"
    );
    assert_eq!(
        edited(&"k".repeat(4096), json!(1)),
        format!("unknown parameter: 'projections[0].{}'", "k".repeat(128))
    );
    assert_eq!(dropped("turn"), "missing parameter: 'projections[0].turn'");
    assert_eq!(dropped("kind"), "missing parameter: 'projections[0].kind'");
    assert_eq!(dropped("text"), "missing parameter: 'projections[0].text'");
    for (key, value, why) in [
        (
            "turn",
            Value::Null,
            "parameter 'projections[0].turn' must be a non-negative integer",
        ),
        (
            "turn",
            json!("3"),
            "parameter 'projections[0].turn' must be a non-negative integer",
        ),
        (
            "span",
            Value::Null,
            "parameter 'projections[0].span' must be a non-negative integer",
        ),
        (
            "span",
            json!(1.5),
            "parameter 'projections[0].span' must be a non-negative integer",
        ),
        (
            "kind",
            Value::Null,
            "parameter 'projections[0].kind' must be a string",
        ),
        (
            "text",
            Value::Null,
            "parameter 'projections[0].text' must be a string",
        ),
        (
            "text",
            json!(7),
            "parameter 'projections[0].text' must be a string",
        ),
        ("turn", json!(0), "projections[0]: turn must be >= 1, got 0"),
        (
            "span",
            json!(0),
            "projections[0]: span must satisfy 1 <= span <= turn, got span=0 for turn=3",
        ),
        (
            "span",
            json!(4),
            "projections[0]: span must satisfy 1 <= span <= turn, got span=4 for turn=3",
        ),
        (
            "kind",
            json!("note"),
            "projections[0]: kind must be one of ['digest', 'epoch', 'log'], got 'note'",
        ),
    ] {
        assert_eq!(edited(key, value.clone()), why, "{key} {value}");
    }
    assert_eq!(
        edited("kind", json!("n".repeat(4096))),
        format!(
            "projections[0]: kind must be one of ['digest', 'epoch', 'log'], got '{}'",
            "n".repeat(128)
        )
    );
    assert_eq!(
        refused(entry(json!("epoch"))),
        "projections[0] must be an object"
    );
}

#[test]
fn trail_acts_are_decoded_strictly() {
    let entry = |item: Value| with(json!({"redactions_from": 0, "redactions": [item]}));
    let whole = json!({
        "conversation_id": "c", "turns": [2], "actor": "client:ops",
        "created_at": "2026-10-05T08:00:00Z",
    });
    assert!(decoded(entry(whole.clone())).is_ok());
    let edited = |key: &str, value: Value| {
        let mut item = whole.clone();
        item[key] = value;
        refused(entry(item))
    };
    let dropped = |key: &str| {
        let mut item = whole.clone();
        item.as_object_mut().unwrap().shift_remove(key);
        refused(entry(item))
    };
    assert_eq!(
        edited("count", json!(1)),
        "unknown parameter: 'redactions[0].count'"
    );
    assert_eq!(
        dropped("conversation_id"),
        "missing parameter: 'redactions[0].conversation_id'"
    );
    assert_eq!(dropped("turns"), "missing parameter: 'redactions[0].turns'");
    assert_eq!(
        dropped("created_at"),
        "missing parameter: 'redactions[0].created_at'"
    );
    let turns = "redactions[0]: turns must name turn numbers from 1";
    for (key, value, why) in [
        (
            "conversation_id",
            Value::Null,
            "parameter 'redactions[0].conversation_id' must be a string",
        ),
        (
            "conversation_id",
            json!("d"),
            "redactions[0]: a trail act names conversation \"d\", not this one",
        ),
        (
            "turns",
            Value::Null,
            "parameter 'redactions[0].turns' must be a list",
        ),
        ("turns", json!([]), turns),
        ("turns", json!([0]), turns),
        ("turns", json!([1, "2"]), turns),
        ("turns", json!([1, null]), turns),
        (
            "actor",
            json!(7),
            "parameter 'redactions[0].actor' must be a string or null",
        ),
        (
            "created_at",
            Value::Null,
            "parameter 'redactions[0].created_at' must be a string",
        ),
        (
            "created_at",
            json!(7),
            "parameter 'redactions[0].created_at' must be a string",
        ),
        (
            "created_at",
            json!("2026-10-05T08:00:00"),
            "redactions[0]: naive wire timestamp refused: '2026-10-05T08:00:00'",
        ),
        (
            "created_at",
            json!("soon"),
            "redactions[0]: Invalid isoformat string: 'soon'",
        ),
        // Ruling R25: nothing of a time is dropped silently.
        (
            "created_at",
            json!("2026-10-05T08:00:00.1234567Z"),
            "redactions[0]: a time has at most six fractional digits: '2026-10-05T08:00:00.1234567Z'",
        ),
        (
            "created_at",
            json!("0000-01-01T00:00:00+00:01"),
            "redactions[0]: a time is within years 0000 to 9999 in UTC: '0000-01-01T00:00:00+00:01'",
        ),
        (
            "created_at",
            json!("9999-12-31T23:59:59-00:01"),
            "redactions[0]: a time is within years 0000 to 9999 in UTC: '9999-12-31T23:59:59-00:01'",
        ),
    ] {
        assert_eq!(edited(key, value.clone()), why, "{key} {value}");
    }
    // What a refusal repeats of the request is cut to 128 scalars.
    assert_eq!(
        edited("conversation_id", json!("d".repeat(4096))),
        format!(
            "redactions[0]: a trail act names conversation {:?}, not this one",
            "d".repeat(128)
        )
    );
    assert_eq!(
        edited("created_at", json!("s".repeat(4096))),
        format!(
            "redactions[0]: Invalid isoformat string: '{}'",
            "s".repeat(128)
        )
    );
    assert_eq!(refused(entry(json!([]))), "redactions[0] must be an object");
}

#[test]
fn the_item_count_is_checked_before_any_item_is_decoded() {
    // 2,049 items that are no headers at all: the count answers first.
    let many: Vec<Value> = (0..PART_ITEMS + 1).map(|_| json!("x")).collect();
    assert_eq!(
        refused(with(json!({"turns": many}))),
        "a part carries at most 2048 items"
    );
    let split = with(json!({
        "turns": vec![json!("x"); 1024],
        "records": vec![json!("x"); 1024],
        "projections_from": 0,
        "projections": [json!("x")],
    }));
    assert_eq!(refused(split), "a part carries at most 2048 items");
    let at: Vec<Value> = (0..PART_ITEMS).map(|_| json!("x")).collect();
    assert_eq!(
        refused(with(json!({"turns": at}))),
        "a turn header must be an object"
    );
}

#[test]
fn an_entry_is_named_by_its_place_in_its_list() {
    let good = json!({"turn": 1, "kind": "log", "text": "t"});
    let second = with(json!({
        "projections_from": 0,
        "projections": [good, {"turn": 1, "kind": "log"}],
    }));
    assert_eq!(refused(second), "missing parameter: 'projections[1].text'");
    let act = json!({"conversation_id": "c", "turns": [1], "created_at": "2026-10-05T08:00:00Z"});
    let mut bad = act.clone();
    bad["turns"] = json!([]);
    let third = with(json!({"redactions_from": 0, "redactions": [act.clone(), act, bad]}));
    assert_eq!(
        refused(third),
        "redactions[2]: turns must name turn numbers from 1"
    );
    // The widest times a restore takes are the first and last microsecond.
    for time in [
        "0000-01-01T00:00:00Z",
        "9999-12-31T23:59:59.999999Z",
        "9999-12-31T22:59:59-01:00",
    ] {
        let one = json!({"conversation_id": "c", "turns": [1], "created_at": time});
        assert!(decoded(with(json!({"redactions_from": 0, "redactions": [one]}))).is_ok());
    }
}
