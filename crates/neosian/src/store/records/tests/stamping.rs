//! Who a draft belongs to (DESIGN §40.54 item 9 "Actor", ruling D1): an
//! opening stores the actor it names, else the caller's bare stamp, as
//! `append_turn` stamps; a later request is compared only when it names one.

use super::{LIMITS, enabled, example, turns};
use crate::conversation::Error;
use crate::conversation::turn_records::{State, TurnRecord, Write, Written, records_of};
use crate::store::FileStore;

const CLIENT: &str = "client:default";
const NAMED: &str = "client:default/claude-code:t";

fn send(
    store: &FileStore,
    draft: &str,
    records: &[TurnRecord],
    actor: Option<&str>,
    seal: bool,
) -> Result<Written, Error> {
    let write = Write {
        draft: draft.into(),
        records: records.to_vec(),
        seal,
        actor: actor.map(str::to_string),
        stamp: Some(CLIENT.into()),
        ..Write::default()
    };
    store.write_records("c-1", write, &LIMITS)
}

fn reason(result: Result<Written, Error>) -> String {
    match result.unwrap_err() {
        Error::Conversation(refused) => {
            assert_eq!(refused.details["draft"], "named");
            refused.details["reason"].as_str().unwrap().to_string()
        }
        other => panic!("{}", other.text()),
    }
}

#[test]
fn an_opening_that_names_no_actor_stores_the_bare_stamp() {
    let (_home, store) = enabled("records-stamp-bare");
    let records = records_of(&example()).unwrap();
    send(&store, "bare", &records[..2], None, false).unwrap();
    let again = send(&store, "bare", &records[..2], None, false).unwrap();
    assert_eq!((again.state, again.stored_bytes), (State::Open, 0));
    let sealed = send(&store, "bare", &records[2..], None, true).unwrap();
    let header = sealed.turn.unwrap();
    assert_eq!(header.actor.as_deref(), Some(CLIENT));
    assert_eq!(turns(&store, "c-1")[0].actor.as_deref(), Some(CLIENT));
    let probe = send(&store, "bare", &[], None, false).unwrap();
    assert_eq!(probe.turn.unwrap().actor.as_deref(), Some(CLIENT));
}

#[test]
fn a_later_request_is_compared_only_when_it_names_an_actor() {
    let (_home, store) = enabled("records-stamp-named");
    let records = records_of(&example()).unwrap();
    send(&store, "named", &records[..2], Some(NAMED), false).unwrap();
    send(&store, "named", &records[2..4], None, false).unwrap();
    send(&store, "named", &records[4..6], Some(NAMED), false).unwrap();
    let other = send(&store, "named", &records[6..8], Some(CLIENT), false);
    assert_eq!(reason(other), "draft_actor");
    let sealed = send(&store, "named", &records[6..], None, true).unwrap();
    assert_eq!(sealed.turn.unwrap().actor.as_deref(), Some(NAMED));
    send(&store, "named", &[], None, false).unwrap();
    send(&store, "named", &[], Some(NAMED), false).unwrap();
    let other = send(&store, "named", &[], Some("client:other"), false);
    assert_eq!(reason(other), "draft_actor");
}

#[test]
fn a_bare_draft_refuses_a_later_named_actor() {
    let (_home, store) = enabled("records-stamp-refused");
    let records = records_of(&example()).unwrap();
    let write = |actor| {
        let write = Write {
            draft: "named".into(),
            records: records[..1].to_vec(),
            actor,
            stamp: Some(CLIENT.into()),
            ..Write::default()
        };
        store.write_records("c-1", write, &LIMITS)
    };
    write(None).unwrap();
    assert_eq!(reason(write(Some(NAMED.to_string()))), "draft_actor");
    write(Some(CLIENT.to_string())).unwrap();
}

#[test]
fn a_discard_is_checked_like_any_later_request() {
    let (_home, store) = enabled("records-stamp-discard");
    let records = records_of(&example()).unwrap();
    let discard = |draft: &str, actor: Option<&str>, turn_bytes| {
        let write = Write {
            draft: draft.into(),
            discard: true,
            actor: actor.map(str::to_string),
            stamp: Some(CLIENT.into()),
            turn_bytes,
            ..Write::default()
        };
        store.write_records("c-1", write, &LIMITS)
    };
    let size = |result: Result<Written, Error>| match result.unwrap_err() {
        Error::Conversation(refused) => refused.details.clone(),
        other => panic!("{}", other.text()),
    };
    send(&store, "named", &records[..2], Some(NAMED), false).unwrap();
    assert_eq!(reason(discard("named", Some(CLIENT), None)), "draft_actor");
    let named = size(discard("named", None, Some(7)));
    assert_eq!(
        (&named["reason"], &named["turn_bytes"], &named["bytes"]),
        (&"draft_size".into(), &7.into(), &36.into())
    );
    let probe = send(&store, "named", &[], None, false).unwrap();
    assert_eq!((probe.state, probe.next_seq), (State::Open, 2));
    let gone = discard("named", Some(NAMED), None).unwrap();
    assert_eq!((gone.state, gone.next_seq), (State::Discarded, 2));
    let pinned = Write {
        draft: "pinned".into(),
        records: records[..2].to_vec(),
        turn_bytes: Some(812_400),
        stamp: Some(CLIENT.into()),
        ..Write::default()
    };
    store.write_records("c-1", pinned, &LIMITS).unwrap();
    let other = size(discard("pinned", None, Some(812_401)));
    assert_eq!(
        (&other["reason"], &other["turn_bytes"], &other["bytes"]),
        (&"draft_size".into(), &812_401.into(), &36.into())
    );
    let gone = discard("pinned", None, Some(812_400)).unwrap();
    assert_eq!(gone.state, State::Discarded);
}
