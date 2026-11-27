//! What the probe finds before a queued turn is written (DESIGN §40.54
//! items 17.3 and 17.4; rulings E8 and E9 of the T1e1 plan, E8 replaced by
//! the rows of 17.4): a draft an earlier run sealed is finished once; a
//! resumed part repeats record `next_seq - 1`, so a draft that is not this
//! turn's is refused (`fresh.rs` writes the turn again under the fresh id,
//! and `held.rs` holds it when the fresh id is refused too); `drafts_open`
//! leaves the turn queued; a draft swept between parts, or gone after
//! recorded progress, starts again from `seq` 0.

use std::fs;

use serde_json::{Value, json};

use super::super::pack::{Draft, turn_bytes};
use super::super::queue::Queue;
use super::super::seal::{Sent, send, send_with};
use super::super::sink::Sink;
use super::super::state::State;
use super::land::{prompts, turn};
use super::records::{enabled, queued, rows, tiny, write};
use super::{ID, settings, state};
use crate::conversation::message::{Message, Role};
use crate::conversation::turn_records::{Kind, TurnRecord, Write, records_of};
use crate::record::settings::Settings;
use crate::remote::selection::Selection;
use crate::store::FileStore;
use crate::testing::Scratch;

/// A write of `state`'s draft, opened by an earlier run.
pub(super) fn earlier(state: &State, records: Vec<TurnRecord>) -> Write {
    Write {
        draft: state.draft.clone(),
        records,
        actor: Some(state.actor.clone()),
        ..Write::default()
    }
}

/// The turn's records with its first prompt's text replaced, as a store
/// would hold records that are not this turn's.
pub(super) fn planted(messages: &[Message], text: &str) -> Vec<TurnRecord> {
    let mut records = records_of(messages).unwrap();
    records[0].text = text.into();
    records[0].size = text.len() as u64;
    records
}

pub(super) fn reason(settings: &Settings, n: u64) -> Value {
    let file = Queue::new(&settings.spool)
        .held_dir(ID)
        .join(format!("{n}.reason"));
    serde_json::from_str(&fs::read_to_string(file).unwrap()).unwrap()
}

#[test]
fn a_draft_sealed_by_an_earlier_run_is_finished_and_stored_once() {
    let scratch = Scratch::new("resume-sealed");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (state, messages) = queued(&settings, "sealed before");
    // The earlier run's seal reached the store; its answer did not. What
    // the store sealed differs from the turn, so only the probe, never a
    // part sent again, can finish it.
    let records = planted(&messages, "sealed as the store has it");
    let total = turn_bytes(&records);
    let seal = Write {
        turn_bytes: Some(total),
        seal: true,
        ..earlier(&state, records)
    };
    write(&root, seal);
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(2));
    assert_eq!(prompts(&root), ["sealed as the store has it", "after"]);
    let queue = Queue::new(&settings.spool);
    assert!(!queue.pending_dir(ID).exists() && !queue.held_dir(ID).exists());
    let store = FileStore::open(&root).unwrap();
    let document = store.read("user:me", &format!("sessions/{ID}")).unwrap();
    assert!(document.unwrap().content.contains("- turns: 2"));
}

#[test]
fn a_resume_repeats_only_the_record_before_next_seq() {
    let scratch = Scratch::new("resume-repeat");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (state, messages) = queued(&settings, "the real prompt");
    // Record 0 differs, record 1 is the turn's: the resume from 2 repeats
    // record 1 alone and never meets record 0.
    write(
        &root,
        earlier(&state, planted(&messages, "a planted prompt")),
    );
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(2));
    assert_eq!(prompts(&root), ["a planted prompt", "after"]);
}

#[test]
fn a_draft_gone_after_recorded_progress_starts_again_from_seq_0() {
    let scratch = Scratch::new("resume-expired");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (mut state, _) = queued(&settings, "the real prompt");
    // An earlier run recorded progress; the store has no draft any more.
    state.next_seq = 3;
    let file = Queue::new(&settings.spool).pending_dir(ID).join("1.state");
    state.write(&file).unwrap();
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(2));
    assert_eq!(prompts(&root), ["the real prompt", "after"]);
    assert_eq!(rows(&root)[0]["draft"], json!(state.draft));
}

#[test]
fn drafts_open_leaves_the_turn_queued_and_the_stop_fails_as_before() {
    let scratch = Scratch::new("resume-drafts-open");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let others: Vec<String> = (0..16).map(|n| format!("other-{n}")).collect();
    let prompt = records_of(&[Message::text(Role::User, "elsewhere".into())]).unwrap();
    for other in &others {
        let records = prompt.clone();
        write(
            &root,
            Write {
                draft: other.clone(),
                records,
                ..Write::default()
            },
        );
    }
    let failed = turn(&settings, "waits").err().expect("the Stop fails");
    assert_eq!(
        failed,
        format!("[agent_conversation_conflict] Conversation conflict on '{ID}': drafts_open")
    );
    let queue = Queue::new(&settings.spool);
    assert_eq!(queue.pending(ID).unwrap(), [1]);
    assert!(!queue.held_dir(ID).exists());
    for other in &others {
        write(
            &root,
            Write {
                draft: other.clone(),
                discard: true,
                ..Write::default()
            },
        );
    }
    assert_eq!(turn(&settings, "then").unwrap().turn, Some(2));
    assert_eq!(prompts(&root), ["waits", "then"]);
}

#[test]
fn a_draft_swept_between_parts_starts_again_from_seq_0() {
    let scratch = Scratch::new("resume-swept");
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
    // After the first part the draft goes, as the store's sweep removes it.
    let mut swept = false;
    let mut sweep = || {
        if !swept {
            swept = true;
            let gone = Write {
                draft: draft_id.clone(),
                discard: true,
                ..Write::default()
            };
            write(&root, gone);
        }
    };
    let sent = send_with(
        &keeper,
        &draft,
        records.clone(),
        &mut state,
        &file,
        &mut sweep,
    );
    // `draft_gap`: the draft expired, and the turn starts again (17.4).
    assert!(matches!(sent.unwrap(), Sent::Restart));
    assert_eq!(State::read(&file).unwrap().next_seq, 2_048);
    let Sent::Sealed(header) = send(&keeper, &draft, records, &mut state, &file).unwrap() else {
        panic!("sealed")
    };
    assert_eq!((header.turn, header.records), (1, 4_097));
    let store = FileStore::open(&root).unwrap();
    assert_eq!(store.read_turns(ID, 0, None).unwrap().len(), 1);
}
