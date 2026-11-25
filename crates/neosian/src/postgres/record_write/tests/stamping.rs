//! Ruling D1 on Postgres: the draft row and the sealed turn keep the actor
//! the opening named, else the caller's bare stamp, and a later request is
//! compared only when it names one.
use super::*;

const CLIENT: &str = "client:default";
const NAMED: &str = "client:default/claude-code:t";

fn stamped(draft: &str, records: &[TurnRecord], actor: Option<&str>) -> Write {
    Write {
        actor: actor.map(str::to_string),
        stamp: Some(CLIENT.into()),
        ..part(draft, records)
    }
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn an_opening_stores_the_named_actor_or_the_bare_stamp() {
    let db = Database::new();
    let store = &db.store;
    let rows = data();
    send(store, stamped("bare", &rows[..1], None)).unwrap();
    let kept: Option<String> =
        db.query("SELECT actor FROM {{schema}}.record_drafts WHERE name='bare'")[0].get(0);
    assert_eq!(kept.as_deref(), Some(CLIENT));
    let repeat = send(store, stamped("bare", &rows[..1], None)).unwrap();
    assert_eq!((repeat.state, repeat.stored_bytes), (State::Open, 0));
    conflict(
        send(store, stamped("bare", &rows[1..2], Some(NAMED))),
        "draft_actor",
    );
    send(store, stamped("bare", &rows[1..2], Some(CLIENT))).unwrap();
    let sealed = send(
        store,
        Write {
            seal: true,
            ..stamped("bare", &rows[2..], None)
        },
    )
    .unwrap();
    assert_eq!(sealed.turn.unwrap().actor.as_deref(), Some(CLIENT));
    let read = store.read_turns("c", 0, None).unwrap();
    assert_eq!(read[0].actor.as_deref(), Some(CLIENT));

    send(store, stamped("named", &rows[..1], Some(NAMED))).unwrap();
    send(store, stamped("named", &rows[1..2], None)).unwrap();
    conflict(
        send(store, stamped("named", &rows[2..], Some(CLIENT))),
        "draft_actor",
    );
    let sealed = send(
        store,
        Write {
            seal: true,
            ..stamped("named", &rows[2..], None)
        },
    )
    .unwrap();
    assert_eq!(sealed.turn.unwrap().actor.as_deref(), Some(NAMED));
    let probe = send(store, stamped("named", &[], None)).unwrap();
    assert_eq!(probe.turn.unwrap().actor.as_deref(), Some(NAMED));
    conflict(
        send(store, stamped("named", &[], Some("client:other"))),
        "draft_actor",
    );
}

#[test]
#[ignore = "requires NEOSIAN_TEST_POSTGRES_DSN"]
fn a_discard_is_checked_like_any_later_request() {
    let db = Database::new();
    let store = &db.store;
    let rows = data();
    let discard = |draft: &str, actor: Option<&str>, turn_bytes| Write {
        discard: true,
        turn_bytes,
        ..stamped(draft, &[], actor)
    };
    send(store, stamped("named", &rows[..2], Some(NAMED))).unwrap();
    conflict(
        send(store, discard("named", Some(CLIENT), None)),
        "draft_actor",
    );
    let named = conflict(send(store, discard("named", None, Some(7))), "draft_size");
    assert_eq!(
        (&named.details["turn_bytes"], &named.details["bytes"]),
        (&json!(7), &json!(6))
    );
    let probe = send(store, stamped("named", &[], None)).unwrap();
    assert_eq!((probe.state, probe.next_seq), (State::Open, 2));
    let gone = send(store, discard("named", Some(NAMED), None)).unwrap();
    assert_eq!((gone.state, gone.next_seq), (State::Discarded, 2));
    send(
        store,
        Write {
            turn_bytes: Some(11),
            ..stamped("pinned", &rows[..2], None)
        },
    )
    .unwrap();
    let other = conflict(send(store, discard("pinned", None, Some(12))), "draft_size");
    assert_eq!(other.details["turn_bytes"], 12);
    let gone = send(store, discard("pinned", None, Some(11))).unwrap();
    assert_eq!(gone.state, State::Discarded);
}
