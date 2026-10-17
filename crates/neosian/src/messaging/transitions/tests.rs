use super::*;
use crate::store::time::Stamp;

fn at(text: &str) -> Stamp {
    Stamp::parse(text).unwrap()
}

fn item(actionable: bool, delivery: Delivery) -> InboxMessage {
    InboxMessage {
        id: "45d78ebb-d289-4f34-981a-43f857ff8cf8".to_string(),
        scope: "user:me".to_string(),
        conversation: None,
        turn: None,
        source: Some("sender".to_string()),
        about: None,
        about_turn: None,
        actor: "cli:local".to_string(),
        body: "one check".to_string(),
        created_at: at("2026-10-03T00:00:00Z"),
        due_at: at("2026-10-03T00:00:00Z"),
        delivery,
        actionable,
        occurrence: 1,
        status: "open".to_string(),
        read_by: None,
        read_at: None,
        owner: None,
        claim_token: None,
        claim_until: None,
        outcome: None,
        wake_owner: None,
        wake_token: None,
        wake_until: None,
        wake_accepted: false,
    }
}

fn by<'a>(session: &'a str, now: &str, occurrence: i64) -> Move<'a> {
    Move {
        session,
        occurrence,
        token: None,
        now: at(now),
        outcome: None,
        due_at: None,
    }
}

#[test]
fn an_ack_reads_once_and_a_second_ack_changes_nothing() {
    let message = item(false, Delivery::NextActivity);
    let read = transition(
        &message,
        Action::Ack,
        &by("recipient", "2026-10-03T01:00:00Z", 1),
    )
    .unwrap();
    assert_eq!(read.read_by.as_deref(), Some("recipient"));
    assert!(!read.unread(at("2026-10-03T02:00:00Z")));
    let again = transition(
        &read,
        Action::Ack,
        &by("recipient", "2026-10-03T02:00:00Z", 1),
    )
    .unwrap();
    assert_eq!(again, read);
    assert_eq!(
        transition(&message, Action::Claim, &by("x", "2026-10-03T01:00:00Z", 1)).unwrap_err(),
        "message is informational; acknowledge it instead"
    );
    assert_eq!(
        transition(&message, Action::Ack, &by("x", "2026-10-03T01:00:00Z", 2)).unwrap_err(),
        "stale occurrence; view the message again"
    );
    assert_eq!(
        transition(&message, Action::Ack, &by("x", "2026-10-02T00:00:00Z", 1)).unwrap_err(),
        "message is not due yet"
    );
}

#[test]
fn a_claim_leases_the_work_and_the_token_fences_it() {
    let message = item(true, Delivery::NextActivity);
    let claimed = transition(
        &message,
        Action::Claim,
        &by("left", "2026-10-03T01:00:00Z", 1),
    )
    .unwrap();
    let token = claimed.claim_token.clone().unwrap();
    assert_eq!(claimed.owner.as_deref(), Some("left"));
    assert_eq!(claimed.claim_until.unwrap().z(), "2026-10-03T01:30:00Z");
    assert_eq!(
        transition(
            &claimed,
            Action::Claim,
            &by("right", "2026-10-03T01:01:00Z", 1)
        )
        .unwrap_err(),
        "message is claimed by another session"
    );
    assert_eq!(
        transition(
            &claimed,
            Action::Claim,
            &by("left", "2026-10-03T01:01:00Z", 1)
        )
        .unwrap(),
        claimed
    );
    let renewed = transition(
        &claimed,
        Action::Renew,
        &Move {
            token: Some(&token),
            ..by("left", "2026-10-03T01:20:00Z", 1)
        },
    )
    .unwrap();
    assert_eq!(renewed.claim_until.unwrap().z(), "2026-10-03T01:50:00Z");
    // The lease lapses: another session claims, and the old token is late.
    let late = by("right", "2026-10-03T02:00:00Z", 1);
    assert!(claimed.unread(late.now));
    let taken = transition(&claimed, Action::Claim, &late).unwrap();
    assert_eq!(taken.owner.as_deref(), Some("right"));
    assert_eq!(
        transition(
            &taken,
            Action::Complete,
            &Move {
                token: Some(&token),
                outcome: Some("late"),
                ..by("left", "2026-10-03T02:01:00Z", 1)
            }
        )
        .unwrap_err(),
        "a current claim token owned by this session is required"
    );
    assert_eq!(
        transition(
            &claimed,
            Action::Complete,
            &Move {
                token: Some(&token),
                ..by("left", "2026-10-03T01:02:00Z", 1)
            }
        )
        .unwrap_err(),
        "completion requires an outcome"
    );
    let done = transition(
        &claimed,
        Action::Complete,
        &Move {
            token: Some(&token),
            outcome: Some("released"),
            ..by("left", "2026-10-03T01:02:00Z", 1)
        },
    )
    .unwrap();
    assert_eq!(
        (done.status.as_str(), done.outcome.as_deref()),
        ("done", Some("released"))
    );
    // Completing done work again with the same token is settled, not refused.
    assert_eq!(
        transition(
            &done,
            Action::Complete,
            &Move {
                token: Some(&token),
                ..by("left", "2026-10-03T01:03:00Z", 1)
            }
        )
        .unwrap(),
        done
    );
    assert_eq!(
        transition(&done, Action::Ack, &by("left", "2026-10-03T01:03:00Z", 1)).unwrap_err(),
        "message is done"
    );
}

#[test]
fn a_snooze_resets_the_reminder_and_a_release_frees_it() {
    let message = item(true, Delivery::NextActivity);
    let claimed = transition(&message, Action::Claim, &by("s", "2026-10-03T01:00:00Z", 1)).unwrap();
    let token = claimed.claim_token.clone().unwrap();
    let snooze = |outcome: Option<&str>, due: Option<&str>| {
        transition(
            &claimed,
            Action::Snooze,
            &Move {
                token: Some(&token),
                outcome,
                due_at: due.map(at),
                ..by("s", "2026-10-03T01:01:00Z", 1)
            },
        )
    };
    assert_eq!(
        snooze(Some("x"), None).unwrap_err(),
        "snooze requires a future due time"
    );
    assert_eq!(
        snooze(Some("x"), Some("2026-10-03T01:00:00Z")).unwrap_err(),
        "snooze requires a future due time"
    );
    assert_eq!(
        snooze(Some("  "), Some("2026-10-17T00:00:00Z")).unwrap_err(),
        "snooze requires the latest finding"
    );
    let reset = snooze(Some("checked; still no fix"), Some("2026-10-17T00:00:00Z")).unwrap();
    assert_eq!(
        (reset.occurrence, reset.due_at.z().as_str()),
        (2, "2026-10-17T00:00:00Z")
    );
    assert!(reset.owner.is_none() && reset.read_at.is_none() && reset.claim_token.is_none());
    assert!(!reset.unread(at("2026-10-10T00:00:00Z")) && reset.unread(at("2026-10-17T00:00:01Z")));
    let released = transition(
        &claimed,
        Action::Release,
        &Move {
            token: Some(&token),
            ..by("s", "2026-10-03T01:01:00Z", 1)
        },
    )
    .unwrap();
    assert!(released.owner.is_none() && released.unread(at("2026-10-03T01:02:00Z")));
    let cancelled = transition(
        &message,
        Action::Cancel,
        &Move {
            outcome: Some("withdrawn"),
            ..by("sender", "2026-10-01T00:00:00Z", 1)
        },
    )
    .unwrap();
    assert_eq!(
        (cancelled.status.as_str(), cancelled.outcome.as_deref()),
        ("cancelled", Some("withdrawn"))
    );
    assert_eq!(
        transition(
            &cancelled,
            Action::Cancel,
            &by("sender", "2026-10-01T00:00:00Z", 1)
        )
        .unwrap(),
        cancelled
    );
}

#[test]
fn a_wake_is_reserved_once_then_accepted_or_retried() {
    let message = item(false, Delivery::Wake);
    let now = "2026-10-03T01:00:00Z";
    assert_eq!(
        transition(
            &item(false, Delivery::NextActivity),
            Action::Reserve,
            &by("r", now, 1)
        )
        .unwrap_err(),
        "no wake is pending"
    );
    let reserved = transition(&message, Action::Reserve, &by("r", now, 1)).unwrap();
    let token = reserved.wake_token.clone().unwrap();
    assert_eq!(reserved.wake_until.unwrap().z(), "2026-10-03T01:01:00Z");
    assert_eq!(
        transition(
            &reserved,
            Action::Reserve,
            &by("other", "2026-10-03T01:00:30Z", 1)
        )
        .unwrap_err(),
        "wake is reserved by another receiver"
    );
    assert_eq!(
        transition(
            &reserved,
            Action::Accepted,
            &Move {
                token: Some("stale"),
                ..by("r", "2026-10-03T01:00:30Z", 1)
            }
        )
        .unwrap_err(),
        "a current wake reservation is required"
    );
    assert_eq!(
        transition(
            &reserved,
            Action::Accepted,
            &Move {
                token: Some(&token),
                ..by("r", "2026-10-03T01:02:00Z", 1)
            }
        )
        .unwrap_err(),
        "a current wake reservation is required"
    );
    let accepted = transition(
        &reserved,
        Action::Accepted,
        &Move {
            token: Some(&token),
            ..by("r", "2026-10-03T01:00:30Z", 1)
        },
    )
    .unwrap();
    assert!(
        accepted.wake_accepted && accepted.wake_owner.is_none() && accepted.wake_until.is_none()
    );
    assert_eq!(
        transition(
            &accepted,
            Action::Reserve,
            &by("r", "2026-10-03T01:05:00Z", 1)
        )
        .unwrap_err(),
        "no wake is pending"
    );
    let retried = transition(
        &reserved,
        Action::Retry,
        &Move {
            token: Some(&token),
            ..by("r", "2026-10-03T01:00:30Z", 1)
        },
    )
    .unwrap();
    assert!(!retried.wake_accepted && retried.wake_token.is_none());
    assert!(
        transition(
            &retried,
            Action::Reserve,
            &by("r", "2026-10-03T01:05:00Z", 1)
        )
        .is_ok()
    );
}
