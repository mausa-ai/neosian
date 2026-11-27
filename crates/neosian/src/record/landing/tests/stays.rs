//! What keeps a held turn held when it is asked back (DESIGN §40.54 item
//! 17.6; ruling "Held selectors" of the T1e2 plan): a turn held because its
//! policy changed goes back only under its fingerprint, for the
//! conversation its profile derives; a paused conversation, a damaged turn,
//! a busy conversation and an answer that may have stored the turn keep it
//! held, and a turn chosen by another opening line is not touched.

use std::fs;

use super::super::draft::draft_id;
use super::super::queue::Queue;
use super::super::requeue::{
    Ask, BUSY, Chosen, DAMAGED, Found, GONE, MAY_BE_STORED, Outcome, PAUSED, POLICY, discard, retry,
};
use super::super::run::UNREADABLE_ANSWER;
use super::super::state::{Destination, POLICY_CHANGED, Reason, State};
use super::requeue::{LINE, ask, back, chosen, closed, plant, refused, stays, whole};
use super::{ID, spool};
use crate::testing::Scratch;

const PROFILED: &str = "w-11111111222243338444555555555555-\
                        29d58b67856cebece6d32bd4459f752da27de5f33f300638842b57d414ab0440";

/// The state of a turn of `PROFILED` held because its policy changed.
fn changed() -> State {
    State {
        draft: draft_id(PROFILED, LINE),
        next_seq: 0,
        fingerprint: Some("old".into()),
        destination: None,
        conversation_id: PROFILED.into(),
        scope: None,
        mount: None,
        provenance: None,
        project: None,
        ..closed()
    }
}

#[test]
fn a_turn_held_for_its_policy_goes_back_only_under_its_fingerprint() {
    let scratch = Scratch::new("requeue-policy");
    let tree = scratch.path().join("profile-spool");
    plant(&tree, PROFILED, 1, &changed(), &Reason::of(POLICY_CHANGED));
    let destination = Destination::Profile {
        name: "work".into(),
        project: "/work".into(),
        source: Some("history".into()),
    };
    let offer = |fingerprint: &str, conversation: &str| Found {
        fingerprint: fingerprint.into(),
        conversation_id: conversation.into(),
        destination: destination.clone(),
        scope: "user:work".into(),
        mount: "history".into(),
        provenance: "\n- workspace: w\n".into(),
        project: "/work".into(),
    };
    let target = chosen(&tree, PROFILED, 1);
    for (fingerprint, conversation) in [("new", PROFILED), ("old", ID)] {
        let find = |_: &State| Ok(offer(fingerprint, conversation));
        let ask = Ask {
            duplicate: false,
            find: &find,
        };
        assert_eq!(stays(retry(&target, &ask, |_| back())), POLICY);
        assert!(whole(&tree, PROFILED, 1, &changed()));
    }
    assert_eq!(stays(retry(&target, &ask(false), |_| back())), POLICY);
    let find = |_: &State| Ok(offer("old", PROFILED));
    let ask = Ask {
        duplicate: false,
        find: &find,
    };
    let outcome = retry(&target, &ask, |_| back()).unwrap();
    assert!(
        matches!(outcome, Outcome::Retried { n: 2, .. }),
        "{outcome:?}"
    );
    let pending = Queue::new(&tree).pending_dir(PROFILED);
    let state = State::read(&Queue::part(&pending, 2, "state")).unwrap();
    let filled = State {
        destination: Some(destination.clone()),
        scope: Some("user:work".into()),
        mount: Some("history".into()),
        provenance: Some("\n- workspace: w\n".into()),
        project: Some("/work".into()),
        ..changed()
    };
    assert_eq!(state, filled);
}

#[test]
fn a_paused_damaged_or_busy_turn_stays_held() {
    let scratch = Scratch::new("requeue-stays");
    let tree = spool(scratch.path());
    let target = chosen(&tree, ID, 1);
    plant(&tree, ID, 1, &closed(), &refused());
    fs::write(tree.join(format!("{ID}.paused")), "").unwrap();
    assert_eq!(stays(retry(&target, &ask(false), |_| back())), PAUSED);
    fs::remove_file(tree.join(format!("{ID}.paused"))).unwrap();
    {
        let _held = Queue::new(&tree).try_lock(ID).unwrap().unwrap();
        assert_eq!(stays(retry(&target, &ask(false), |_| back())), BUSY);
        assert_eq!(stays(discard(&target)), BUSY);
    }
    let mut other = closed();
    other.draft = draft_id(ID, "{\"kind\": \"span\"}");
    let mut short = closed();
    short.bytes -= 1;
    let mut nowhere = closed();
    nowhere.destination = None;
    for damaged in [other, short, nowhere] {
        plant(&tree, ID, 1, &damaged, &refused());
        assert_eq!(stays(retry(&target, &ask(false), |_| back())), DAMAGED);
        assert!(whole(&tree, ID, 1, &damaged));
    }
    plant(&tree, ID, 1, &closed(), &refused());
    let reason = Queue::part(&Queue::new(&tree).held_dir(ID), 1, "reason");
    fs::remove_file(&reason).unwrap();
    assert_eq!(stays(retry(&target, &ask(false), |_| back())), DAMAGED);
    fs::write(&reason, refused().text()).unwrap();
    let wrong = Chosen {
        line: Some("{\"kind\": \"span\", \"nonce\": \"another\"}"),
        ..chosen(&tree, ID, 1)
    };
    assert_eq!(stays(retry(&wrong, &ask(false), |_| back())), GONE);
    assert_eq!(stays(discard(&chosen(&tree, ID, 7))), GONE);
}

#[test]
fn an_answer_that_may_have_stored_the_turn_goes_back_only_when_asked() {
    let scratch = Scratch::new("requeue-duplicate");
    let tree = spool(scratch.path());
    let target = chosen(&tree, ID, 1);
    plant(
        &tree,
        ID,
        1,
        &closed(),
        &Reason::unreadable(UNREADABLE_ANSWER.into()),
    );
    assert_eq!(
        stays(retry(&target, &ask(false), |_| back())),
        MAY_BE_STORED
    );
    assert!(whole(&tree, ID, 1, &closed()));
    let outcome = retry(&target, &ask(true), |_| back()).unwrap();
    assert!(
        matches!(outcome, Outcome::Retried { n: 2, .. }),
        "{outcome:?}"
    );
}
