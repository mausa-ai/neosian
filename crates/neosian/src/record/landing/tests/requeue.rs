//! A held turn put back or deleted (DESIGN §40.54 item 17.6; ruling "Held
//! selectors" of the T1e2 plan): a retry takes the next number, never one
//! a lander may hold, and keeps the turn's bytes, close time and draft; a
//! cut on either side of the rename leaves one whole turn; a discard
//! deletes exactly the turn named; a turn put back lands through the
//! ordinary landing. What keeps a turn held is `stays.rs`.

use std::fs::{self, TryLockError};
use std::io;
use std::path::Path;

use super::super::aside::{held, said};
use super::super::cap::Cap;
use super::super::draft::draft_id;
use super::super::queue::Queue;
use super::super::requeue::{Ask, Chosen, Found, GONE, Outcome, Recorder, discard, retry};
use super::super::state::{Reason, State, UNREADABLE, describe};
use super::super::turn::land_queue;
use super::land::prompts;
use super::{ID, emptied, settings, spool, state};
use crate::record::land::here;
use crate::store::fileio::private;
use crate::testing::Scratch;

pub(super) const LINE: &str = "{\"kind\": \"span\", \"opened_at\": \"2026-11-26T11:00:00.000000Z\", \
                               \"nonce\": \"0b8f6a2e-1c3d-4e5f-8a6b-7c8d9e0f1a2b\"}";

pub(super) fn bytes() -> Vec<u8> {
    format!("{LINE}\n{{\"kind\": \"prompt\", \"text\": \"held\"}}\n").into_bytes()
}

/// A direct state of `ID` for the turn `bytes()`, closed an hour before
/// the turns of the other tests, with progress on its draft.
pub(super) fn closed() -> State {
    let mut closed = state(LINE, bytes().len() as u64);
    closed.closed_at = "2026-11-26T11:00:00.000000Z".into();
    closed.next_seq = 3;
    closed
}

pub(super) fn refused() -> Reason {
    Reason::refused(Some(400), Some("value_error".into()), "refused here".into())
}

/// Held turn `n` of `id` in `tree` with `state` and `reason`.
pub(super) fn plant(tree: &Path, id: &str, n: u64, state: &State, reason: &Reason) {
    let directory = Queue::new(tree).held_dir(id);
    fs::create_dir_all(&directory).unwrap();
    fs::write(Queue::part(&directory, n, "jsonl"), bytes()).unwrap();
    state.write(&Queue::part(&directory, n, "state")).unwrap();
    fs::write(Queue::part(&directory, n, "reason"), reason.text()).unwrap();
}

pub(super) fn chosen<'a>(tree: &'a Path, id: &'a str, n: u64) -> Chosen<'a> {
    Chosen {
        tree,
        recorder: Recorder::Direct,
        id,
        n,
        line: Some(LINE),
    }
}

fn nowhere(_: &State) -> Result<Found, String> {
    Err("no profile is selected here".into())
}

pub(super) fn ask(duplicate: bool) -> Ask<'static> {
    Ask {
        duplicate,
        find: &nowhere,
    }
}

pub(super) fn stays(outcome: io::Result<Outcome>) -> &'static str {
    match outcome.unwrap() {
        Outcome::Stays { code, .. } => code,
        other => panic!("{other:?}"),
    }
}

/// Whether held turn `n` of `id` is whole, as planted.
pub(super) fn whole(tree: &Path, id: &str, n: u64, state: &State) -> bool {
    let directory = Queue::new(tree).held_dir(id);
    fs::read(Queue::part(&directory, n, "jsonl")).ok() == Some(bytes())
        && State::read(&Queue::part(&directory, n, "state"))
            .ok()
            .as_ref()
            == Some(state)
        && Queue::part(&directory, n, "reason").is_file()
}

pub(super) fn back() -> io::Result<()> {
    Ok(())
}

#[test]
fn a_retry_takes_the_next_number_and_keeps_the_turn_as_it_closed() {
    let scratch = Scratch::new("requeue-number");
    let tree = spool(scratch.path());
    plant(&tree, ID, 1, &closed(), &refused());
    // A later turn waits, and a pause removed 3 and 4 while a lander held
    // them: the counter keeps their numbers (ruling G1).
    let queue = Queue::new(&tree);
    let pending = queue.pending_dir(ID);
    fs::create_dir_all(&pending).unwrap();
    fs::write(Queue::part(&pending, 2, "jsonl"), bytes()).unwrap();
    state(LINE, 1)
        .write(&Queue::part(&pending, 2, "state"))
        .unwrap();
    fs::write(pending.join(".next"), "5\n").unwrap();
    let outcome = retry(&chosen(&tree, ID, 1), &ask(false), |_| back()).unwrap();
    let length = bytes().len() as u64;
    assert_eq!(
        outcome,
        Outcome::Retried {
            n: 5,
            bytes: length
        }
    );
    assert_eq!(
        fs::read(Queue::part(&pending, 5, "jsonl")).unwrap(),
        bytes()
    );
    let state = State::read(&Queue::part(&pending, 5, "state")).unwrap();
    assert_eq!(state, closed());
    // Its reason goes with it: the keep limit reads that its number does
    // not follow when it closed.
    let reason = fs::read_to_string(Queue::part(&pending, 5, "reason")).unwrap();
    assert_eq!(reason, refused().text());
    assert!(!queue.held_dir(ID).exists());
    assert_eq!(queue.pending(ID).unwrap(), [2, 5]);
}

#[test]
fn a_cut_before_the_rename_leaves_the_held_turn_whole() {
    let scratch = Scratch::new("requeue-cut-before");
    let tree = spool(scratch.path());
    plant(&tree, ID, 1, &closed(), &refused());
    let cut = |step: &str| match step {
        "staged" => Err(io::Error::other("cut")),
        _ => Ok(()),
    };
    assert!(retry(&chosen(&tree, ID, 1), &ask(false), cut).is_err());
    assert!(whole(&tree, ID, 1, &closed()));
    let queue = Queue::new(&tree);
    let pending = queue.pending_dir(ID);
    assert!(Queue::part(&pending, 2, "state").is_file());
    assert!(Queue::part(&pending, 2, "reason").is_file());
    assert!(!Queue::part(&pending, 2, "jsonl").exists());
    assert_eq!(queue.pending(ID).unwrap(), Vec::<u64>::new());
    assert_eq!(held(&tree).unwrap().len(), 1);
    // The next retry takes a number past the staged state.
    let outcome = retry(&chosen(&tree, ID, 1), &ask(false), |_| back()).unwrap();
    let length = bytes().len() as u64;
    assert_eq!(
        outcome,
        Outcome::Retried {
            n: 3,
            bytes: length
        }
    );
}

#[test]
fn a_cut_after_the_rename_leaves_one_pending_turn() {
    let scratch = Scratch::new("requeue-cut-after");
    let tree = spool(scratch.path());
    plant(&tree, ID, 1, &closed(), &refused());
    let cut = |step: &str| match step {
        "moved" => Err(io::Error::other("cut")),
        _ => Ok(()),
    };
    assert!(retry(&chosen(&tree, ID, 1), &ask(false), cut).is_err());
    let queue = Queue::new(&tree);
    let pending = queue.pending_dir(ID);
    assert_eq!(
        fs::read(Queue::part(&pending, 2, "jsonl")).unwrap(),
        bytes()
    );
    let state = State::read(&Queue::part(&pending, 2, "state")).unwrap();
    assert_eq!(state, closed());
    assert_eq!(queue.pending(ID).unwrap(), [2]);
    // The held state and reason are left without their turn: no held turn.
    let directory = queue.held_dir(ID);
    assert!(!Queue::part(&directory, 1, "jsonl").exists());
    assert!(Queue::part(&directory, 1, "reason").is_file());
    assert!(held(&tree).unwrap().is_empty());
}

#[test]
fn a_discard_deletes_exactly_the_turn_named() {
    let scratch = Scratch::new("requeue-discard");
    let tree = spool(scratch.path());
    plant(&tree, ID, 1, &closed(), &refused());
    plant(&tree, ID, 2, &closed(), &refused());
    let length = bytes().len() as u64;
    let outcome = discard(&chosen(&tree, ID, 1)).unwrap();
    assert_eq!(outcome, Outcome::Discarded { bytes: length });
    let directory = Queue::new(&tree).held_dir(ID);
    for part in ["jsonl", "state", "reason"] {
        assert!(!Queue::part(&directory, 1, part).exists());
    }
    assert!(whole(&tree, ID, 2, &closed()));
    assert_eq!(stays(discard(&chosen(&tree, ID, 1))), GONE);
    // Its conversation's pending directory, made for the lock, is gone.
    assert!(emptied(&Queue::new(&tree).pending_dir(ID)));
}

#[test]
fn a_turn_put_back_lands_through_the_ordinary_landing() {
    let scratch = Scratch::new("requeue-lands");
    let settings = settings(scratch.path(), None);
    let mut closed = closed();
    closed.destination = Some(describe(&settings.store));
    closed.next_seq = 0;
    plant(&settings.spool, ID, 1, &closed, &refused());
    let outcome = retry(&chosen(&settings.spool, ID, 1), &ask(false), |_| back()).unwrap();
    assert!(
        matches!(outcome, Outcome::Retried { n: 2, .. }),
        "{outcome:?}"
    );
    let queue = Queue::new(&settings.spool);
    let run = land_queue(&queue, ID, &here(&settings, &Cap::NONE)).unwrap();
    assert_eq!(run.unwrap().landed.len(), 1);
    assert_eq!(prompts(&scratch.path().join("store")), ["held"]);
    assert!(!Queue::new(&settings.spool).held_dir(ID).exists());
}

/// A direct session id may have a profile conversation id's shape.
const SHAPED: &str = "w-11111111222243338444555555555555-\
                      29d58b67856cebece6d32bd4459f752da27de5f33f300638842b57d414ab0440";

#[test]
fn a_retry_holds_the_session_lock_its_recorder_takes_at_a_stop() {
    let key = &SHAPED[35..];
    for (recorder, locked) in [
        (Recorder::Direct, [true, false]),
        (Recorder::Profile, [false, true]),
        (Recorder::Unknown, [true, true]),
    ] {
        let scratch = Scratch::new(&format!("requeue-session-{recorder:?}"));
        let tree = spool(scratch.path());
        let mut state = closed();
        (state.conversation_id, state.draft) = (SHAPED.into(), draft_id(SHAPED, LINE));
        plant(&tree, SHAPED, 1, &state, &refused());
        let target = Chosen {
            recorder,
            ..chosen(&tree, SHAPED, 1)
        };
        // Between the staged state and the rename, a Stop of the session
        // (the id's lock) or of the profile conversation (the key's) waits.
        let mut taken = Vec::new();
        let outcome = retry(&target, &ask(false), |step| {
            for name in [SHAPED, key].into_iter().filter(|_| step == "staged") {
                let path = tree.join(format!(".{name}.lock"));
                let file = private().write(true).create(true).open(path)?;
                taken.push(matches!(file.try_lock(), Err(TryLockError::WouldBlock)));
            }
            Ok(())
        });
        assert!(matches!(outcome.unwrap(), Outcome::Retried { n: 2, .. }));
        assert_eq!(taken, locked, "{recorder:?}");
    }
}

#[test]
fn a_state_written_for_another_turn_under_its_number_never_sends_it() {
    let scratch = Scratch::new("requeue-replaced");
    let settings = settings(scratch.path(), None);
    // A Stop's state, whose turn a retry's rename replaced: the same length,
    // another opening line.
    let theirs = LINE.replace("0b8f6a2e", "9a7c5e3d");
    let mut stop = closed();
    stop.destination = Some(describe(&settings.store));
    stop.draft = draft_id(ID, &theirs);
    let queue = Queue::new(&settings.spool);
    let pending = queue.pending_dir(ID);
    fs::create_dir_all(&pending).unwrap();
    fs::write(Queue::part(&pending, 1, "jsonl"), bytes()).unwrap();
    stop.write(&Queue::part(&pending, 1, "state")).unwrap();
    let run = land_queue(&queue, ID, &here(&settings, &Cap::NONE)).unwrap();
    assert!(run.unwrap().landed.is_empty());
    assert!(prompts(&scratch.path().join("store")).is_empty());
    let kept = said(&queue.held_dir(ID), 1).expect("held with a reason");
    assert_eq!(kept.reason, UNREADABLE);
    assert_eq!(
        fs::read(Queue::part(&queue.held_dir(ID), 1, "jsonl")).unwrap(),
        bytes()
    );
}
