//! The lander's own bounds (DESIGN §40.54 items 17.3 and 23; rulings D4
//! and A.5.16 of the T1e1 plan): two slots per home, a third lander finds
//! none and lands nothing; ten minutes on an injected wall clock, a clock
//! set back ending the run too; a run past its cap lands no turn, and one
//! that reaches it between parts finishes the part in flight and keeps the
//! turn with the `next_seq` that part confirmed, for the next run. A
//! paused conversation is not landed (the lander holds no session lock),
//! and a pause between parts discards the draft (ruling E19).

use std::cell::Cell;
use std::fs;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::Value;

use super::super::cap::{Cap, Halt, PASSED};
use super::super::pack::{Draft, turn_bytes};
use super::super::queue::Queue;
use super::super::rules::Stop;
use super::super::seal::{Sent, send, send_with};
use super::super::sink::Sink;
use super::super::slots;
use super::super::turn::land_queue;
use super::super::verb::{Land, run};
use super::land::{prompts, turn};
use super::records::{enabled, tiny};
use super::{ID, settings, state};
use crate::conversation::turn_records::{Kind, TurnRecord};
use crate::record::land::here;
use crate::remote::selection::Selection;
use crate::store::FileStore;
use crate::testing::Scratch;

thread_local! {
    static NOW: Cell<SystemTime> = const { Cell::new(UNIX_EPOCH) };
}

/// The injected wall clock: what `at` set last on this thread.
fn clock() -> SystemTime {
    NOW.with(Cell::get)
}

fn at(seconds: u64) {
    NOW.with(|now| now.set(UNIX_EPOCH + Duration::from_secs(seconds)));
}

fn summary(land: &Land) -> Value {
    let (mut out, mut err) = (Vec::new(), Vec::new());
    assert_eq!(run(land, &mut out, &mut err).unwrap(), 0);
    serde_json::from_slice(&out).unwrap()
}

#[test]
fn a_third_lander_finds_no_slot_and_lands_nothing() {
    let scratch = Scratch::new("lander-slots");
    let settings = settings(scratch.path(), None);
    let queue = Queue::new(&settings.spool);
    let lock = queue.try_lock(ID).unwrap().unwrap();
    assert_eq!(turn(&settings, "waits").unwrap().disposition, "pending");
    drop(lock);
    let home = scratch.path();
    let first = slots::take(home).unwrap().expect("a slot");
    let second = slots::take(home).unwrap().expect("another slot");
    assert_eq!((first.number, second.number), (1, 2));
    assert!(slots::take(home).unwrap().is_none());
    assert!(!slots::free(home).unwrap());
    let land = Land {
        home: home.to_path_buf(),
        spool: Some(settings.spool.clone()),
        url: None,
    };
    let third = summary(&land);
    assert_eq!(
        (&third["slot"], &third["landed"]),
        (&Value::Null, &0.into())
    );
    assert_eq!(queue.pending(ID).unwrap(), [1]);
    drop(second);
    // Read once released: Windows refuses a read of a locked file.
    let pid = format!("{}\n", std::process::id());
    let slot = slots::directory(home).join("slot-2.lock");
    assert_eq!(fs::read_to_string(slot).unwrap(), pid);
    assert!(slots::free(home).unwrap());
    let landed = summary(&land);
    assert_eq!((&landed["slot"], &landed["landed"]), (&2.into(), &1.into()));
    assert_eq!(prompts(&scratch.path().join("store")), ["waits"]);
}

#[test]
fn ten_minutes_pass_on_the_wall_clock_and_a_clock_set_back_ends_the_run() {
    at(1_000);
    let cap = Cap::on(clock);
    at(1_599);
    assert!(!cap.passed());
    at(1_600);
    assert!(cap.passed());
    at(999);
    assert!(cap.passed());
    assert!(!Cap::NONE.passed());
}

#[test]
fn a_run_past_its_cap_lands_no_turn() {
    let scratch = Scratch::new("lander-capped");
    let settings = settings(scratch.path(), None);
    let queue = Queue::new(&settings.spool);
    let lock = queue.try_lock(ID).unwrap().unwrap();
    for prompt in ["one", "two"] {
        assert_eq!(turn(&settings, prompt).unwrap().disposition, "pending");
    }
    drop(lock);
    at(0);
    let cap = Cap::on(clock);
    at(600);
    let run = land_queue(&queue, ID, &here(&settings, &cap))
        .unwrap()
        .unwrap();
    assert!(run.landed.is_empty());
    assert!(matches!(run.stopped, Some(Stop::Conversation(m)) if m == PASSED));
    assert_eq!(queue.pending(ID).unwrap(), [1, 2]);
    let run = land_queue(&queue, ID, &here(&settings, &Cap::NONE)).unwrap();
    assert_eq!(run.unwrap().landed.len(), 2);
    assert_eq!(prompts(&scratch.path().join("store")), ["one", "two"]);
}

#[test]
fn the_cap_reached_between_parts_finishes_the_part_in_flight_and_the_next_run_resumes() {
    let scratch = Scratch::new("lander-cap-parts");
    let root = enabled(scratch.path());
    let mut state = state("{\"kind\": \"span\"}", 1);
    let file = scratch.path().join("1.state");
    state.write(&file).unwrap();
    // 2,048 records to a part: three parts.
    let records: Vec<TurnRecord> = (0..4_097).map(|seq| tiny(seq, Kind::Prompt)).collect();
    let sink = Sink::open(&Selection::File(root.clone())).unwrap();
    let keeper = sink.records().unwrap().unwrap();
    let draft_id = state.draft.clone();
    let draft = Draft {
        conversation: ID,
        draft: &draft_id,
        actor: "claude-code:x",
        turn_bytes: turn_bytes(&records),
    };
    at(0);
    let cap = Cap::on(clock);
    // The first part is in flight when the ten minutes pass.
    let mut late = || at(600);
    let sent = send_with(
        &keeper,
        &draft,
        records.clone(),
        &mut state,
        &file,
        &Halt {
            cap: &cap,
            paused: None,
        },
        &mut late,
    );
    assert!(matches!(sent.unwrap(), Sent::Kept(Stop::Conversation(m)) if m == PASSED));
    assert_eq!(state.next_seq, 2_048);
    let stored = FileStore::open(&root).unwrap();
    assert!(stored.read_turns(ID, 0, None).unwrap().is_empty());
    let sent = send(&keeper, &draft, records, &mut state, &file, &Halt::NONE);
    let Sent::Sealed(header) = sent.unwrap() else {
        panic!("sealed")
    };
    assert_eq!((header.turn, header.records), (1, 4_097));
}

#[test]
fn a_paused_conversation_is_not_landed() {
    let scratch = Scratch::new("lander-paused");
    let settings = settings(scratch.path(), None);
    let queue = Queue::new(&settings.spool);
    let lock = queue.try_lock(ID).unwrap().unwrap();
    assert_eq!(turn(&settings, "private").unwrap().disposition, "pending");
    drop(lock);
    fs::write(settings.spool.join(format!("{ID}.paused")), "").unwrap();
    let run = land_queue(&queue, ID, &here(&settings, &Cap::NONE)).unwrap();
    let run = run.unwrap();
    assert!(run.paused && run.landed.is_empty());
    assert_eq!(queue.pending(ID).unwrap(), [1]);
}

#[test]
fn a_pause_between_parts_discards_the_draft_and_sends_no_further_part() {
    let scratch = Scratch::new("lander-pause-parts");
    let root = enabled(scratch.path());
    let mut state = state("{\"kind\": \"span\"}", 1);
    let file = scratch.path().join("1.state");
    state.write(&file).unwrap();
    let records: Vec<TurnRecord> = (0..4_097).map(|seq| tiny(seq, Kind::Prompt)).collect();
    let sink = Sink::open(&Selection::File(root.clone())).unwrap();
    let keeper = sink.records().unwrap().unwrap();
    let draft_id = state.draft.clone();
    let draft = Draft {
        conversation: ID,
        draft: &draft_id,
        actor: "claude-code:x",
        turn_bytes: turn_bytes(&records),
    };
    let marker = scratch.path().join(format!("{ID}.paused"));
    let halt = Halt {
        cap: &Cap::NONE,
        paused: Some(&marker),
    };
    // The pause is given while the first part is in flight.
    let mut pause = || fs::write(&marker, "").unwrap();
    let sent = send_with(
        &keeper, &draft, records, &mut state, &file, &halt, &mut pause,
    );
    assert!(matches!(sent.unwrap(), Sent::Paused));
    let probe = keeper.write(ID, draft.probe()).unwrap();
    let super::super::answer::Answer::Written(probe) = probe else {
        panic!("answered")
    };
    assert_eq!(probe.state.as_str(), "none");
    let stored = FileStore::open(&root).unwrap();
    assert!(stored.read_turns(ID, 0, None).unwrap().is_empty());
}
