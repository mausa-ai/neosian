//! A seal sent, then its turn erased (finding 1 of the T1 review): the
//! lander marks the state before it sends a seal, so a probe that later
//! finds no draft, in a conversation the store has erased turns of, holds
//! the turn as maybe stored instead of sending the erased turn again; only
//! a retry that allows a duplicate puts it back. Where nothing was erased
//! the seal never arrived, and the turn starts again. A refusal of the seal
//! clears the mark, so a draft the store refused starts again as before.

use std::path::Path;

use super::super::cap::{Cap, Halt};
use super::super::pack::{Draft, turn_bytes};
use super::super::queue::Queue;
use super::super::requeue::{Chosen, Outcome, Recorder, retry};
use super::super::seal::{SEAL_GONE, Sent, send};
use super::super::sink::Sink;
use super::super::state::State;
use super::super::turn::{Run, land_queue};
use super::land::prompts;
use super::records::{enabled, line_of, queued};
use super::requeue::ask;
use super::resume::reason;
use super::{ID, settings};
use crate::conversation::turn_records::records_of;
use crate::record::land::here;
use crate::record::settings::Settings;
use crate::remote::selection::Selection;
use crate::store::FileStore;
use crate::testing::Scratch;

/// Turn 1 queued, then sent by `send` with its stated size raised by
/// `over`: what `send` answered and the state file it left.
fn sent(settings: &Settings, root: &Path, over: u64) -> (Sent, std::path::PathBuf) {
    let (mut state, messages) = queued(settings, "erase me");
    let file = Queue::new(&settings.spool).pending_dir(ID).join("1.state");
    let sink = Sink::open(&Selection::File(root.to_path_buf())).unwrap();
    let keeper = sink.records().unwrap().unwrap();
    let turn = records_of(&messages).unwrap();
    let (draft_id, actor) = (state.draft.clone(), state.actor.clone());
    let draft = Draft {
        conversation: ID,
        draft: &draft_id,
        actor: &actor,
        turn_bytes: turn_bytes(&turn) + over,
    };
    let sent = send(&keeper, &draft, turn, &mut state, &file, &Halt::NONE).unwrap();
    (sent, file)
}

fn run(settings: &Settings) -> Run {
    let queue = Queue::new(&settings.spool);
    land_queue(&queue, ID, &here(settings, &Cap::NONE))
        .unwrap()
        .unwrap()
}

#[test]
fn an_erased_turn_whose_seal_was_sent_is_held_and_never_sent_again() {
    let scratch = Scratch::new("sealing-erased");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    // The seal reaches the store and the lander dies before it removes the
    // queued copy; the person then erases the turn and its receipt goes.
    let (sealed, file) = sent(&settings, &root, 0);
    assert!(matches!(sealed, Sent::Sealed(_)));
    assert!(State::read(&file).unwrap().sealing);
    let line = line_of(&settings, 1);
    let store = FileStore::open(&root).unwrap();
    assert_eq!(store.redact_turns(ID, None, Some(&[1]), None).unwrap(), 1);
    let first = run(&settings);
    assert!(first.landed.is_empty(), "the erased turn was sent again");
    assert_eq!(first.refused.len(), 1);
    let held = reason(&settings, 1);
    assert_eq!(
        (&held["reason"], &held["may_be_stored"]),
        (&"unreadable".into(), &true.into())
    );
    assert_eq!(held["message"], SEAL_GONE);
    let turns = store.read_turns(ID, 0, None).unwrap();
    assert_eq!((turns.len(), turns[0].redacted), (1, true));
    // A retry without a duplicate allowed leaves it; one with lands it again.
    let chosen = Chosen {
        tree: &settings.spool,
        recorder: Recorder::Direct,
        id: ID,
        n: 1,
        line: Some(&line),
    };
    let stays = retry(&chosen, &ask(false), |_| Ok(())).unwrap();
    assert!(
        matches!(
            stays,
            Outcome::Stays {
                code: "may_be_stored",
                ..
            }
        ),
        "{stays:?}"
    );
    let back = retry(&chosen, &ask(true), |_| Ok(())).unwrap();
    assert!(matches!(back, Outcome::Retried { n: 2, .. }), "{back:?}");
    assert_eq!(run(&settings).landed.len(), 1);
    assert_eq!(store.read_turns(ID, 0, None).unwrap().len(), 2);
}

#[test]
fn a_seal_the_store_refuses_clears_its_mark() {
    let scratch = Scratch::new("sealing-refused");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    // A stated size the records do not make: the seal is refused
    // `draft_size`, and the turn is written again under the fresh id.
    let (refused, file) = sent(&settings, &root, 1);
    assert!(matches!(refused, Sent::Fresh(_)));
    assert!(!State::read(&file).unwrap().sealing);
    assert_eq!(run(&settings).landed.len(), 1);
}

#[test]
fn a_seal_that_never_arrived_starts_again_where_nothing_was_erased() {
    let scratch = Scratch::new("sealing-lost");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    // An earlier run marked its seal and lost the answer; the seal never
    // reached the store, which holds no draft of it and erased nothing.
    let (mut state, _) = queued(&settings, "never arrived");
    state.sealing = true;
    let file = Queue::new(&settings.spool).pending_dir(ID).join("1.state");
    state.write(&file).unwrap();
    assert_eq!(run(&settings).landed.len(), 1);
    assert_eq!(prompts(&root), ["never arrived"]);
}
