use super::*;

const WRITTEN: &str = r#"{
  "format": 1,
  "id": "45d78ebb-d289-4f34-981a-43f857ff8cf8",
  "scope": "user:me/proj:demo",
  "conversation": null,
  "turn": null,
  "source": null,
  "about": "early",
  "about_turn": 1,
  "actor": "cli:local",
  "body": "Check upstream issue #123",
  "created_at": "2026-10-13T21:56:33.425486Z",
  "due_at": "2026-10-13T21:56:33.425486Z",
  "delivery": "next_activity",
  "actionable": false,
  "occurrence": 1,
  "status": "open",
  "read_by": null,
  "read_at": null,
  "owner": null,
  "claim_token": null,
  "claim_until": null,
  "outcome": null,
  "wake_owner": null,
  "wake_token": null,
  "wake_until": null,
  "wake_accepted": false
}
"#;

fn with(field: &str, value: &str) -> String {
    WRITTEN.replace(
        &format!("\"{field}\": null"),
        &format!("\"{field}\": {value}"),
    )
}

fn at(text: &str) -> Stamp {
    Stamp::parse(text).unwrap()
}

#[test]
fn a_message_reads_back_as_pydantic_wrote_it() {
    let message = decode(WRITTEN).unwrap();
    assert_eq!(message.id, "45d78ebb-d289-4f34-981a-43f857ff8cf8");
    assert_eq!(
        (
            message.about.as_deref(),
            message.about_turn,
            message.occurrence
        ),
        (Some("early"), Some(1), 1)
    );
    assert_eq!(message.created_at.z(), "2026-10-13T21:56:33.425486Z");
    assert_eq!((message.status.as_str(), message.outcome), ("open", None));
    assert!(message.conversation.is_none() && !message.actionable);
    assert_eq!(message.delivery, Delivery::NextActivity);
    assert!(message.wake_token.is_none() && !message.wake_accepted);
}

#[test]
fn encode_writes_the_bytes_pydantic_writes() {
    let message = decode(WRITTEN).unwrap();
    assert_eq!(encode(&message), WRITTEN);
    let claimed = with("owner", "\"codex:x\"")
        .replace(
            "\"claim_until\": null",
            "\"claim_until\": \"2026-10-15T00:00:00Z\"",
        )
        .replace(
            "\"body\": \"Check upstream issue #123\"",
            "\"body\": \"é \\\"quoted\\\"\\nline\"",
        );
    assert_eq!(encode(&decode(&claimed).unwrap()), claimed);
    let wake = WRITTEN
        .replace("\"delivery\": \"next_activity\"", "\"delivery\": \"wake\"")
        .replace("\"wake_accepted\": false", "\"wake_accepted\": true");
    assert_eq!(encode(&decode(&wake).unwrap()), wake);
}

#[test]
fn unread_is_due_open_and_not_read_under_a_live_claim() {
    let now = at("2026-10-14T00:00:00Z");
    let message = decode(WRITTEN).unwrap();
    assert!(message.available(now) && message.unread(now) && !message.claimed(now));
    assert!(!message.available(at("2026-10-13T00:00:00Z")));
    assert!(
        !decode(&with("read_at", "\"2026-10-13T22:00:00Z\""))
            .unwrap()
            .unread(now)
    );
    let lapsed = decode(
        &with("read_at", "\"2026-10-13T22:00:00Z\"")
            .replace("\"owner\": null", "\"owner\": \"codex:x\"")
            .replace(
                "\"claim_until\": null",
                "\"claim_until\": \"2026-10-13T23:00:00Z\"",
            ),
    )
    .unwrap();
    assert!(lapsed.unread(now) && !lapsed.claimed(now));
    let held = decode(&with("owner", "\"codex:x\"").replace(
        "\"claim_until\": null",
        "\"claim_until\": \"2026-10-15T00:00:00Z\"",
    ))
    .unwrap();
    assert!(held.claimed(now));
    assert!(
        !decode(&WRITTEN.replace("\"status\": \"open\"", "\"status\": \"done\""))
            .unwrap()
            .available(now)
    );
}

#[test]
fn a_corrupt_document_is_refused_with_a_sentence() {
    assert_eq!(
        decode("not json").err().as_deref(),
        Some("message document is not JSON")
    );
    assert!(decode(&WRITTEN.replace("\"format\": 1", "\"format\": 2")).is_err());
    assert!(decode(&WRITTEN.replace("\"body\": \"Check upstream issue #123\",\n", "")).is_err());
    assert!(decode(&WRITTEN.replace("\"about_turn\": 1", "\"about_turn\": -1")).is_err());
    assert!(decode(&WRITTEN.replace("\"occurrence\": 1", "\"occurrence\": 0")).is_err());
    assert!(decode(&with("source", "\"a/b\"")).is_err());
    assert!(
        decode(&WRITTEN.replace("\"delivery\": \"next_activity\"", "\"delivery\": \"soon\""))
            .is_err()
    );
    assert_eq!(
        message_path("45d78ebb-d289-4f34-981a-43f857ff8cf8").as_deref(),
        Ok("messages/45d78ebb-d289-4f34-981a-43f857ff8cf8")
    );
    assert!(message_path("45D78EBB-d289-4f34-981a-43f857ff8cf8").is_err());
    assert!(message_path("x").is_err());
}

#[test]
fn a_target_is_a_scope_a_conversation_and_a_turn_from_one() {
    assert_eq!(check_target("user:me", None, None), Ok(()));
    assert_eq!(check_target("user:me", Some("s1"), Some(3)), Ok(()));
    assert_eq!(
        check_target("user:me", None, Some(1)).unwrap_err(),
        "a turn reference needs a conversation and turn >= 1"
    );
    assert!(check_target("user:me", Some("s1"), Some(0)).is_err());
    assert!(
        check_target("nope", None, None)
            .unwrap_err()
            .starts_with("Invalid memory scope 'nope'")
    );
    assert!(
        check_target("user:me", Some("a/b"), None)
            .unwrap_err()
            .starts_with("Invalid conversation id 'a/b'")
    );
}

#[test]
fn a_due_time_is_now_the_delay_or_the_moment_named() {
    let now = at("2026-10-03T00:00:00+02:00");
    assert_eq!(
        due_time(now, None, None).unwrap().z(),
        "2026-10-02T22:00:00Z"
    );
    assert_eq!(
        due_time(now, None, Some(90)).unwrap().z(),
        "2026-10-02T22:01:30Z"
    );
    assert_eq!(
        due_time(now, Some(at("2026-11-01T10:00:00+01:00")), None)
            .unwrap()
            .z(),
        "2026-11-01T09:00:00Z"
    );
    assert_eq!(
        due_time(now, Some(now), Some(1)).unwrap_err(),
        "give due_at or delay_seconds, not both"
    );
    assert_eq!(
        due_time(now, None, Some(0)).unwrap_err(),
        "delay_seconds must be positive"
    );
    assert_eq!(Action::parse("snooze"), Some(Action::Snooze));
    assert_eq!(Action::parse("poke"), None);
}
