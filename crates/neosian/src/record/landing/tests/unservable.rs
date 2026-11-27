//! No lander that cannot work, and no number taken twice (rulings G7 and
//! G1 of the T1e1 plan). A queued turn whose state is missing, damaged or
//! names no destination is no hook's to land, and the lander holds it
//! `unreadable` under the conversation's lock; a conversation whose id
//! begins with a dot is landed. A pause that removes pending turns while a
//! lander holds the conversation leaves a counter no later turn numbers
//! below, and the lock's holder removes it with the emptied directory.

use std::fs;

use serde_json::{Value, json};

use super::super::queue::Queue;
use super::super::start::queued;
use super::super::verb::{Land, run};
use super::land::{prompts, turn};
use super::{ID, emptied, record, settings};
use crate::record::land::land;
use crate::record::spool::Spool;
use crate::store::FileStore;
use crate::store::grammar::Actor;
use crate::testing::Scratch;

fn landed(land: &Land) -> Value {
    let (mut out, mut err) = (Vec::new(), Vec::new());
    assert_eq!(run(land, &mut out, &mut err).unwrap(), 0);
    serde_json::from_slice(&out).unwrap()
}

#[test]
fn a_turn_whose_state_cannot_be_read_starts_no_lander_and_a_lander_holds_it() {
    let scratch = Scratch::new("unservable-state");
    let (home, settings) = (scratch.path(), settings(scratch.path(), None));
    let queue = Queue::new(&settings.spool);
    let lock = queue.try_lock(ID).unwrap().unwrap();
    for prompt in ["missing", "damaged", "nowhere"] {
        assert_eq!(turn(&settings, prompt).unwrap().disposition, "pending");
    }
    drop(lock);
    let pending = queue.pending_dir(ID);
    fs::remove_file(Queue::part(&pending, 1, "state")).unwrap();
    fs::write(Queue::part(&pending, 2, "state"), "{").unwrap();
    let state = Queue::part(&pending, 3, "state");
    let mut nowhere: Value = serde_json::from_str(&fs::read_to_string(&state).unwrap()).unwrap();
    nowhere["destination"] = Value::Null;
    fs::write(&state, nowhere.to_string()).unwrap();
    assert!(
        !queued(&settings.spool, home, ID).queued,
        "no turn is landable"
    );
    let land = Land {
        home: home.to_path_buf(),
        spool: Some(settings.spool.clone()),
    };
    let summary = landed(&land);
    assert_eq!(
        (&summary["held"], &summary["landed"]),
        (&3.into(), &0.into())
    );
    assert!(queue.pending(ID).unwrap().is_empty());
    for n in 1..=3 {
        let reason = queue.held_dir(ID).join(format!("{n}.reason"));
        let reason: Value = serde_json::from_str(&fs::read_to_string(reason).unwrap()).unwrap();
        assert_eq!(reason["reason"], "unreadable", "{n}");
    }
}

#[test]
fn a_conversation_whose_id_begins_with_a_dot_is_landed() {
    let scratch = Scratch::new("unservable-dot");
    let settings = settings(scratch.path(), None);
    let (id, spool) = (".session", Spool::new(&settings.spool));
    let actor = Actor::parse("claude-code:dot").unwrap();
    let queue = Queue::new(&settings.spool);
    let lock = queue.try_lock(id).unwrap().unwrap();
    for (event, kind) in [("UserPromptSubmit", "prompt"), ("Stop", "stop")] {
        let line = record(json!({"kind": kind, "text": "dotted"}));
        land(&settings, &spool, id, &actor, event, &line).unwrap();
    }
    drop(lock);
    assert_eq!(queue.pending(id).unwrap(), [1]);
    let land = Land {
        home: scratch.path().to_path_buf(),
        spool: Some(settings.spool.clone()),
    };
    assert_eq!(landed(&land)["landed"], 1);
    let store = FileStore::open(&scratch.path().join("store")).unwrap();
    assert_eq!(store.read_turns(id, 0, None).unwrap().len(), 1);
}

#[test]
fn a_pause_while_a_lander_holds_the_conversation_leaves_a_counter_no_turn_numbers_below() {
    let scratch = Scratch::new("unservable-counter");
    let settings = settings(scratch.path(), None);
    let queue = Queue::new(&settings.spool);
    // The lock free: nothing holds the removed numbers, so 1 is taken again.
    let lock = queue.try_lock(ID).unwrap().unwrap();
    assert_eq!(turn(&settings, "free").unwrap().disposition, "pending");
    drop(lock);
    queue.discard_pending(ID).unwrap();
    assert_eq!(queue.next_n(ID).unwrap(), 1);
    // A lander holds the conversation, sending turn 2.
    let lock = queue.try_lock(ID).unwrap().unwrap();
    for prompt in ["one", "two"] {
        assert_eq!(turn(&settings, prompt).unwrap().disposition, "pending");
    }
    queue.discard_pending(ID).unwrap();
    assert!(queue.pending(ID).unwrap().is_empty());
    assert_eq!(turn(&settings, "after").unwrap().disposition, "pending");
    assert_eq!(queue.pending(ID).unwrap(), [3]);
    drop(lock);
    assert_eq!(turn(&settings, "landed").unwrap().disposition, "recorded");
    assert_eq!(prompts(&scratch.path().join("store")), ["after", "landed"]);
    // Unix: the counter went with the lock (Windows keeps both, `emptied`).
    assert!(emptied(&queue.pending_dir(ID)), "no turn is left queued");
}
