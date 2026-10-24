use super::governance::{KEY, Policy, fingerprint, status, timestamp};
use super::model::Content;
use serde_json::json;

#[test]
fn review_windows_are_half_open_at_microsecond_precision() {
    let mut content = Content::parse("A procedure", "notes").unwrap();
    content.metadata.attributes.insert(
        KEY.into(),
        json!({
            "format":1,"kind":"procedure","owners":["team"],"state":"approved",
            "effective_at":"2026-10-24T00:00:00.000001Z",
            "expires_at":"2026-10-24T00:00:00.000003Z",
            "review":{"by":"reviewer","at":"2026-10-24T00:00:00Z","sha256":"0".repeat(64)}
        }),
    );
    content.metadata.attributes[KEY]["review"]["sha256"] =
        fingerprint("org:test", "notes/a", &content).into();
    let text = content.render().unwrap();
    for (instant, expected) in [
        ("2026-10-24T00:00:00Z", "scheduled"),
        ("2026-10-24T00:00:00.000001Z", "approved"),
        ("2026-10-24T00:00:00.000002Z", "approved"),
        ("2026-10-24T00:00:00.000003Z", "expired"),
    ] {
        assert_eq!(
            status("org:test", "notes/a", &text, timestamp(instant).unwrap()),
            expected
        );
    }
    assert!(!Policy::Approved.accepts("unmanaged"));
    assert!(Policy::Effective.accepts("unmanaged"));
    assert_eq!(Policy::Effective.max(Policy::Approved), Policy::Approved);
}

#[test]
fn governance_dates_are_strict_utc_and_calendar_valid() {
    for text in [
        "",
        "x",
        "2026-10-24 00:00:00Z",
        "2026-10-24T00:00Z",
        "2026-10-24T00:00:00+00:00",
        "2026-02-30T00:00:00Z",
        "2026-10-24T00:00:00.Z",
        "2026-10-24T00:00:00.1234567Z",
        "2026-10-24T00:00:00.éZ",
    ] {
        assert!(timestamp(text).is_err(), "{text}");
    }
    assert!(timestamp("2024-02-29T12:30:00.1Z").is_ok());
}
