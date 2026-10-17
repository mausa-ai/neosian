//! The lifecycle against a real store (`tests/unit/messaging/
//! test_lifecycle.py`'s pins): delivery repeats until acknowledged, a
//! reminder snoozes and completes, a lease is fenced, successors read
//! what strangers may not, scopes stay apart.

use super::*;
use crate::memory::mounts::Mount;
use crate::messaging::delivery::context;
use crate::messaging::mailbox::ListStatus;
use crate::store::FileStore;
use crate::store::grammar::Actor;
use crate::testing::Scratch;

const SCOPE: &str = "user:test/proj:mail";
const USER: &str = "user:test";

fn at(text: &str) -> Stamp {
    Stamp::parse(text).unwrap()
}

fn mounts() -> Vec<Mount> {
    vec![
        Mount::new(SCOPE, "project", (false, false), "").unwrap(),
        Mount::new(USER, "user", (false, false), "").unwrap(),
    ]
}

fn mailbox<'a>(store: &'a FileStore, mounts: &[Mount], session: &str, now: &str) -> Mailbox<'a> {
    Mailbox::of_mounts(store, mounts, Some(session))
        .with_actor(Actor::parse(&format!("test:{session}")).unwrap())
        .at(at(now))
}

fn to<'a>(scope: &'a str, conversation: Option<&'a str>) -> Send<'a> {
    Send {
        scope,
        conversation,
        turn: None,
        delivery: Delivery::NextActivity,
        actionable: false,
        due_at: None,
        delay_seconds: None,
        about: None,
        about_turn: None,
    }
}

fn by(occurrence: i64, token: Option<&str>) -> Update<'_> {
    Update {
        occurrence,
        token,
        outcome: None,
        due_at: None,
        delay_seconds: None,
    }
}

fn value(error: Error) -> String {
    match error {
        Error::Value(sentence) => sentence,
        other => panic!("{}", other.text()),
    }
}

#[test]
fn delivery_repeats_until_acknowledged() {
    let scratch = Scratch::new("mailbox-ack");
    let store = FileStore::open(scratch.path()).unwrap();
    let mounts = mounts();
    let box_ = mailbox(&store, &mounts, "recipient", "2026-10-03T00:00:00Z");
    let receipt = box_
        .send("migration ready", &to(SCOPE, Some("recipient")))
        .unwrap();
    assert_eq!(receipt.version, 1);
    assert_eq!(receipt.message.source.as_deref(), Some("recipient"));
    assert_eq!(receipt.message.about.as_deref(), Some("recipient"));
    assert!(context(&box_).unwrap().contains("migration ready"));
    assert!(context(&box_).unwrap().contains("migration ready"));
    assert_eq!(box_.view(SCOPE, &receipt.message.id).unwrap().version, 1);
    let read = box_
        .update(SCOPE, &receipt.message.id, Action::Ack, &by(1, None))
        .unwrap();
    assert_eq!(read.message.read_by.as_deref(), Some("recipient"));
    assert_eq!(context(&box_).unwrap(), "");
    let again = box_
        .update(SCOPE, &receipt.message.id, Action::Ack, &by(1, None))
        .unwrap();
    assert_eq!(again.version, read.version);
    assert_eq!(box_.history(SCOPE, &receipt.message.id).unwrap().len(), 2);
    assert_eq!(
        value(box_.send("  ", &to(SCOPE, None)).unwrap_err()),
        "message body must not be empty"
    );
    assert_eq!(
        value(box_.send("x", &to("user:other", None)).unwrap_err()),
        "scope is not mounted: user:other"
    );
}

#[test]
fn a_reminder_snoozes_and_completes() {
    let scratch = Scratch::new("mailbox-snooze");
    let store = FileStore::open(scratch.path()).unwrap();
    let mounts = mounts();
    let day = 86_400;
    let early = mailbox(&store, &mounts, "s", "2026-10-03T00:00:00Z");
    let item = early
        .send(
            "check upstream fix",
            &Send {
                delay_seconds: Some(15 * day),
                ..to(SCOPE, None)
            },
        )
        .unwrap()
        .message;
    assert!(item.actionable && item.due_at.z() == "2026-10-18T00:00:00Z");
    assert!(
        early
            .list(None, ListStatus::Unread, 50, true, false)
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        early
            .list(None, ListStatus::Scheduled, 50, true, false)
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        value(
            early
                .update(SCOPE, &item.id, Action::Claim, &by(1, None))
                .unwrap_err()
        ),
        "message is not due yet"
    );
    let due = mailbox(&store, &mounts, "s", "2026-10-18T00:00:00Z");
    assert_eq!(
        due.list(None, ListStatus::Unread, 50, true, false)
            .unwrap()
            .len(),
        1
    );
    let claimed = due
        .update(SCOPE, &item.id, Action::Claim, &by(1, None))
        .unwrap();
    let token = claimed.message.claim_token.clone().unwrap();
    let reset = due
        .update(
            SCOPE,
            &item.id,
            Action::Snooze,
            &Update {
                outcome: Some("checked; still no fix"),
                delay_seconds: Some(14 * day),
                ..by(1, Some(&token))
            },
        )
        .unwrap();
    assert_eq!(reset.message.due_at.z(), "2026-11-01T00:00:00Z");
    assert_eq!(
        (reset.message.occurrence, reset.message.owner.as_deref()),
        (2, None)
    );
    assert!(
        due.list(None, ListStatus::Unread, 50, true, false)
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        value(
            due.update(SCOPE, &item.id, Action::Ack, &by(1, Some(&token)))
                .unwrap_err()
        ),
        "stale occurrence; view the message again"
    );
    let later = mailbox(&store, &mounts, "s", "2026-11-01T00:00:00Z");
    let claimed = later
        .update(SCOPE, &item.id, Action::Claim, &by(2, None))
        .unwrap();
    let token = claimed.message.claim_token.clone().unwrap();
    let done = later
        .update(
            SCOPE,
            &item.id,
            Action::Complete,
            &Update {
                outcome: Some("released"),
                ..by(2, Some(&token))
            },
        )
        .unwrap();
    assert_eq!(done.message.status, "done");
    assert!(
        later
            .list(None, ListStatus::Unread, 50, true, false)
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        later
            .list(None, ListStatus::Closed, 50, true, false)
            .unwrap()
            .len(),
        1
    );
    assert!(
        later
            .history(SCOPE, &item.id)
            .unwrap()
            .iter()
            .any(|version| version.outcome.as_deref() == Some("checked; still no fix"))
    );
    let settled = later
        .update(SCOPE, &item.id, Action::Complete, &by(2, Some(&token)))
        .unwrap();
    assert_eq!(settled.version, done.version);
}

#[test]
fn successors_read_what_strangers_may_not() {
    let scratch = Scratch::new("mailbox-lineage");
    let store = FileStore::open(scratch.path()).unwrap();
    let mounts = mounts();
    let now = "2026-10-03T00:00:00Z";
    let sender = mailbox(&store, &mounts, "sender", now);
    let item = sender
        .send("correction", &to(SCOPE, Some("old")))
        .unwrap()
        .message;
    let successor = mailbox(&store, &mounts, "new", now);
    assert!(
        successor
            .list(None, ListStatus::Unread, 50, true, false)
            .unwrap()
            .is_empty()
    );
    successor.note_continuation(SCOPE, "old").unwrap();
    successor.note_continuation(SCOPE, "old").unwrap(); // once
    assert_eq!(
        successor
            .list(None, ListStatus::Unread, 50, true, false)
            .unwrap()
            .len(),
        1
    );
    mailbox(&store, &mounts, "old", now)
        .note_continuation(SCOPE, "new")
        .unwrap();
    let lineage = successor.lineage(SCOPE).unwrap();
    assert!(lineage.contains("old") && lineage.contains("new") && lineage.len() == 2);
    let stranger = mailbox(&store, &mounts, "stranger", now);
    assert_eq!(
        stranger.view(SCOPE, &item.id).unwrap().message.body,
        "correction"
    );
    assert_eq!(
        value(
            stranger
                .update(SCOPE, &item.id, Action::Ack, &by(1, None))
                .unwrap_err()
        ),
        "this session is not a recipient or explicit successor"
    );
    sender
        .update(SCOPE, &item.id, Action::Cancel, &by(1, None))
        .unwrap();
    assert!(
        successor
            .list(None, ListStatus::Unread, 50, true, false)
            .unwrap()
            .is_empty()
    );
    // No session, no declaration: nothing is written.
    Mailbox::of_mounts(&store, &mounts, None)
        .note_continuation(SCOPE, "x")
        .unwrap();
    assert!(
        store
            .list_documents(SCOPE, "message-links/")
            .unwrap()
            .iter()
            .all(|e| !e.path.ends_with("/x"))
    );
}

#[test]
fn scopes_and_mounts_stay_apart() {
    let scratch = Scratch::new("mailbox-scopes");
    let store = FileStore::open(scratch.path()).unwrap();
    let mounts = mounts();
    let now = "2026-10-03T00:00:00Z";
    let first = mailbox(&store, &mounts, "a", now);
    let shared = first
        .send("shared reminder", &to(USER, None))
        .unwrap()
        .message;
    first.send("private project", &to(SCOPE, None)).unwrap();
    let other = vec![
        Mount::new(USER, "user", (false, false), "").unwrap(),
        Mount::new("user:test/proj:other", "project", (false, false), "").unwrap(),
    ];
    let second = mailbox(&store, &other, "b", now);
    let listed: Vec<String> = second
        .list(None, ListStatus::Unread, 50, true, false)
        .unwrap()
        .into_iter()
        .map(|receipt| receipt.message.id)
        .collect();
    assert_eq!(listed, vec![shared.id.clone()]);
    assert_eq!(
        value(second.view(SCOPE, &shared.id).unwrap_err()),
        format!("scope is not mounted: {SCOPE}")
    );
    assert_eq!(
        value(
            first
                .list(None, ListStatus::Unread, 0, true, false)
                .unwrap_err()
        ),
        "limit must be between 1 and 500"
    );
    let fixed = vec![Mount::new(SCOPE, "project", (false, true), "").unwrap()];
    let edit_only = mailbox(&store, &fixed, "c", now);
    assert_eq!(
        edit_only.send("x", &to(SCOPE, None)).unwrap_err().text(),
        "[memory_edit_only_mount] Memory mount 'project' is edit-only: its document set is fixed"
    );
    edit_only.note_continuation(SCOPE, "a").unwrap(); // nothing written
    assert!(store.read(SCOPE, "message-links/c/a").unwrap().is_none());
}
