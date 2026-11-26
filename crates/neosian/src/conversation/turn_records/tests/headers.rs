//! A turn header as a restore sends it (§40.54 items 3.6, 8.2 and 12):
//! each key, what is refused, and that a decoded header keeps its
//! canonical form.

use serde_json::{Value, json};

use crate::conversation::turn_records::Header;
use crate::store::time::Stamp;

fn whole() -> Value {
    json!({
        "turn": 1,
        "created_at": "2026-10-02T09:15:00.000001Z",
        "actor": "claude-code:7f3a",
        "redacted": false,
        "records": 3,
        "bytes": 1532,
    })
}

fn with(key: &str, value: Value) -> Value {
    let mut header = whole();
    header[key] = value;
    header
}

fn without(key: &str) -> Value {
    let mut header = whole();
    header.as_object_mut().unwrap().shift_remove(key);
    header
}

fn refused(value: Value) -> String {
    Header::from_json(&value).expect_err("the header is refused")
}

#[test]
fn a_header_decodes_key_by_key_and_keeps_its_canonical_form() {
    let header = Header::from_json(&whole()).unwrap();
    assert_eq!(
        header,
        Header {
            turn: 1,
            created_at: Stamp::parse("2026-10-02T09:15:00.000001Z").unwrap(),
            actor: Some("claude-code:7f3a".into()),
            redacted: false,
            records: 3,
            bytes: 1532,
        }
    );
    assert_eq!(
        header.canonical(),
        "{\"turn\":1,\"created_at\":\"2026-10-02T09:15:00.000001Z\",\
         \"actor\":\"claude-code:7f3a\",\"redacted\":false,\"records\":3,\"bytes\":1532}"
    );
    // Key order is free in a request; the canonical form is the table's.
    let reversed: serde_json::Map<String, Value> = whole()
        .as_object()
        .unwrap()
        .iter()
        .rev()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    assert_eq!(Header::from_json(&Value::Object(reversed)).unwrap(), header);
    // The actor alone may be null or absent.
    for value in [with("actor", Value::Null), without("actor")] {
        assert_eq!(Header::from_json(&value).unwrap().actor, None);
    }
    // A time at another offset is the same instant, written with `Z`.
    let east = with("created_at", json!("2026-10-02T11:15:00.000001+02:00"));
    assert_eq!(Header::from_json(&east).unwrap(), header);
    let redacted = json!({
        "turn": 2, "created_at": "2026-10-02T09:20:11Z", "actor": null,
        "redacted": true, "records": 0, "bytes": 0,
    });
    assert_eq!(
        Header::from_json(&redacted).unwrap().canonical(),
        "{\"turn\":2,\"created_at\":\"2026-10-02T09:20:11Z\",\"actor\":null,\
         \"redacted\":true,\"records\":0,\"bytes\":0}"
    );
}

#[test]
fn an_unknown_key_is_refused_and_named_in_at_most_128_scalars() {
    assert_eq!(
        refused(with("zeta", json!(1))),
        "unknown header field \"zeta\""
    );
    assert_eq!(
        refused(with("draft", json!("d"))),
        "unknown header field \"draft\""
    );
    let long = format!("{}{}", "é".repeat(128), "x".repeat(4096));
    assert_eq!(
        refused(with(&long, json!(1))),
        format!("unknown header field {:?}", "é".repeat(128))
    );
    for value in [json!([]), json!("turn"), json!(1), Value::Null] {
        assert_eq!(refused(value), "a turn header must be an object");
    }
}

#[test]
fn every_key_but_the_actor_is_required_and_typed() {
    for key in ["turn", "created_at", "redacted", "records", "bytes"] {
        assert_eq!(
            refused(without(key)),
            format!("header field {key:?} is required")
        );
    }
    let integer = |key: &str| format!("header field {key:?} must be a non-negative integer");
    for key in ["turn", "records", "bytes"] {
        for value in [json!(-1), json!(1.5), json!("1"), json!(true), Value::Null] {
            assert_eq!(refused(with(key, value)), integer(key));
        }
    }
    assert_eq!(
        refused(with("turn", json!(0))),
        "header field \"turn\" must be an integer >= 1"
    );
    let time = "header field \"created_at\" must be an ISO-8601 time with an offset";
    for value in [
        json!("2026-10-02T09:15:00"),
        json!("2026-10-02"),
        json!("yesterday"),
        json!(1_790_000_000),
        Value::Null,
    ] {
        assert_eq!(refused(with("created_at", value)), time);
    }
    for value in [json!(7), json!(["a"]), json!(false)] {
        assert_eq!(
            refused(with("actor", value)),
            "header field \"actor\" must be a string or null"
        );
    }
    for value in [json!(0), json!("false"), Value::Null] {
        assert_eq!(
            refused(with("redacted", value)),
            "header field \"redacted\" must be a boolean"
        );
    }
}

#[test]
fn a_time_is_kept_whole_or_refused() {
    // Ruling R25: more than six fractional digits, and a UTC form outside
    // years 0000 to 9999, are refused; nothing is dropped silently.
    assert_eq!(
        refused(with("created_at", json!("2026-10-02T09:15:00.1234567Z"))),
        "header field \"created_at\" has more than six fractional digits"
    );
    for time in [
        "0000-01-01T00:00:00+00:01",
        "9999-12-31T23:59:59.999999-00:01",
    ] {
        assert_eq!(
            refused(with("created_at", json!(time))),
            "header field \"created_at\" is outside years 0000 to 9999 in UTC"
        );
    }
    for (time, written) in [
        ("2026-10-02T09:15:00.123456Z", "2026-10-02T09:15:00.123456Z"),
        ("2026-10-02T09:15:00.5Z", "2026-10-02T09:15:00.500000Z"),
        ("0000-01-01T00:00:00Z", "0000-01-01T00:00:00Z"),
        ("0000-01-01T01:00:00+01:00", "0000-01-01T00:00:00Z"),
        ("9999-12-31T23:59:59.999999Z", "9999-12-31T23:59:59.999999Z"),
    ] {
        let header = Header::from_json(&with("created_at", json!(time))).unwrap();
        assert_eq!(header.created_at.z(), written);
    }
}

#[test]
fn an_empty_list_placed_past_the_restore_is_a_skip() {
    use crate::conversation::turn_records::{Position, Restore};
    let at = Position {
        projections: 2,
        redactions: 1,
        ..Position::default()
    };
    let placed = |projections_from, redactions_from| Restore {
        projections_from,
        redactions_from,
        ..Restore::default()
    };
    for (projections, redactions, skips) in [
        (None, None, false),
        (Some(0), Some(0), false),
        (Some(2), Some(1), false),
        (Some(3), None, true),
        (None, Some(2), true),
    ] {
        assert_eq!(placed(projections, redactions).skips(at), skips);
    }
    assert!(placed(Some(1), None).skips(Position::default()));
    assert!(!placed(Some(0), Some(0)).skips(Position::default()));
}
