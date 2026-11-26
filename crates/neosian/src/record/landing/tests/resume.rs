//! What the probe finds before a queued turn is written (DESIGN §40.54
//! item 17.3; rulings E8 and E9 of the T1e1 plan): a draft an earlier run
//! sealed is finished once; a resumed part repeats record `next_seq - 1`,
//! so a draft that is not this turn's is refused and the turn held; a draft
//! that cannot be continued is held; `drafts_open` and a swept draft leave
//! the turn queued, and the next run starts again from `seq` 0.

use std::fs;

use serde_json::{Value, json};

use super::super::pack::{Draft, turn_bytes};
use super::super::queue::Queue;
use super::super::seal::{Sent, send, send_with};
use super::super::sink::Sink;
use super::super::state::State;
use super::land::{prompts, turn};
use super::records::{enabled, queued, tiny, write};
use super::{ID, settings, state};
use crate::conversation::message::{Message, Role};
use crate::conversation::turn_records::{Kind, TurnRecord, Write, records_of};
use crate::record::settings::Settings;
use crate::remote::selection::Selection;
use crate::store::FileStore;
use crate::testing::Scratch;

/// A write of `state`'s draft, opened by an earlier run.
fn earlier(state: &State, records: Vec<TurnRecord>) -> Write {
    Write {
        draft: state.draft.clone(),
        records,
        actor: Some(state.actor.clone()),
        ..Write::default()
    }
}

/// The turn's records with its first prompt's text replaced, as a store
/// would hold records that are not this turn's.
fn planted(messages: &[Message], text: &str) -> Vec<TurnRecord> {
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

fn conflict(reason: &str) -> Value {
    json!({"reason": "refused", "status": 400, "code": "agent_conversation_conflict",
        "message": format!("Conversation conflict on '{ID}': {reason}")})
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
fn a_resumed_draft_that_is_not_this_turn_s_is_discarded_and_the_turn_held() {
    let scratch = Scratch::new("resume-mismatch");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (state, messages) = queued(&settings, "the real prompt");
    let mut first = planted(&messages, "a planted prompt");
    first.truncate(1);
    write(&root, earlier(&state, first));
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(1));
    assert_eq!(prompts(&root), ["after"]);
    assert_eq!(reason(&settings, 1), conflict("draft_mismatch"));
    let held = Queue::new(&settings.spool).held_dir(ID);
    assert!(
        fs::read_to_string(held.join("1.jsonl"))
            .unwrap()
            .contains("the real prompt")
    );
    let drafts = root.join("conversations").join(ID).join(".drafts");
    assert!(!drafts.join(format!("{}.state", state.draft)).exists());
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
fn an_unreadable_draft_of_the_turn_is_held_and_later_turns_land() {
    let scratch = Scratch::new("resume-unreadable");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (state, messages) = queued(&settings, "the real prompt");
    let mut first = records_of(&messages).unwrap();
    first.truncate(1);
    write(&root, earlier(&state, first));
    let drafts = root.join("conversations").join(ID).join(".drafts");
    fs::write(drafts.join(format!("{}.state", state.draft)), "not a state").unwrap();
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(1));
    assert_eq!(prompts(&root), ["after"]);
    let message =
        format!("Unsupported turn format in conversation '{ID}': a draft's state is unreadable");
    let code = "agent_conversation_format_unsupported";
    let expected = json!({"reason": "refused", "status": 400, "code": code, "message": message});
    assert_eq!(reason(&settings, 1), expected);
}

#[test]
fn a_draft_longer_than_the_turn_is_discarded_and_the_turn_held() {
    let scratch = Scratch::new("resume-longer");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (state, mut messages) = queued(&settings, "the real prompt");
    messages.push(Message::text(Role::Assistant, "more".into()));
    write(&root, earlier(&state, records_of(&messages).unwrap()));
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(1));
    let message = format!(
        "draft {} holds 3 records, more than the turn's 2",
        state.draft
    );
    let expected = json!({"reason": "refused", "status": null, "code": null, "message": message});
    assert_eq!(reason(&settings, 1), expected);
    let drafts = root.join("conversations").join(ID).join(".drafts");
    assert!(!drafts.join(format!("{}.state", state.draft)).exists());
}

#[test]
fn a_resumed_draft_that_stated_another_total_is_held() {
    let scratch = Scratch::new("resume-size");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (state, messages) = queued(&settings, "the real prompt");
    let records = records_of(&messages).unwrap();
    let other = Write {
        turn_bytes: Some(turn_bytes(&records) + 1),
        ..earlier(&state, records[..1].to_vec())
    };
    write(&root, other);
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(1));
    assert_eq!(prompts(&root), ["after"]);
    assert_eq!(reason(&settings, 1), conflict("draft_size"));
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
fn a_draft_swept_between_parts_stays_queued_and_starts_again_from_seq_0() {
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
    let failed = send_with(
        &keeper,
        &draft,
        records.clone(),
        &mut state,
        &file,
        &mut sweep,
    );
    let failed = failed.err().expect("the run stops").text();
    assert!(failed.ends_with(": draft_gap"), "{failed}");
    assert_eq!(State::read(&file).unwrap().next_seq, 2_048);
    let Sent::Sealed(header) = send(&keeper, &draft, records, &mut state, &file).unwrap() else {
        panic!("sealed")
    };
    assert_eq!((header.turn, header.records), (1, 4_097));
    let store = FileStore::open(&root).unwrap();
    assert_eq!(store.read_turns(ID, 0, None).unwrap().len(), 1);
}
