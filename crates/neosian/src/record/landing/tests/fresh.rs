//! A draft that does not match its turn (DESIGN §40.54 items 17.4 and
//! 25.13; rulings E8, E9 and E15 of the T1e1 plan, E8 replaced by 17.4's
//! rows): `draft_mismatch`, `draft_size`, a draft longer than the turn and a
//! draft of the turn the store cannot read are discarded and the turn
//! written again under the fresh id, once; refused again, it is held
//! `mismatch` (`held.rs` covers the hold and its words).

use std::fs;

use serde_json::json;

use super::super::draft::fresh_id;
use super::super::pack::turn_bytes;
use super::super::queue::Queue;
use super::super::state::State;
use super::land::{prompts, turn};
use super::records::{enabled, line_of, queued, rows, write};
use super::resume::{earlier, planted, reason};
use super::{ID, settings};
use crate::conversation::message::{Message, Role};
use crate::conversation::turn_records::{TurnRecord, Write, records_of};
use crate::record::settings::Settings;
use crate::testing::Scratch;

/// The turn's draft is gone, it landed under the fresh id of its opening
/// line `line`, and nothing of the conversation is queued or held.
fn landed_fresh(settings: &Settings, root: &std::path::Path, state: &State, line: &str) {
    let drafts = root.join("conversations").join(ID).join(".drafts");
    assert!(!drafts.join(format!("{}.state", state.draft)).exists());
    assert_eq!(rows(root)[0]["draft"], json!(fresh_id(ID, line)));
    let queue = Queue::new(&settings.spool);
    assert!(!queue.pending_dir(ID).exists() && !queue.held_dir(ID).exists());
}

#[test]
fn a_resumed_draft_that_is_not_this_turn_s_is_written_again_under_the_fresh_id() {
    let scratch = Scratch::new("resume-mismatch");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (state, messages) = queued(&settings, "the real prompt");
    let line = line_of(&settings, 1);
    let mut first = planted(&messages, "a planted prompt");
    first.truncate(1);
    write(&root, earlier(&state, first));
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(2));
    assert_eq!(prompts(&root), ["the real prompt", "after"]);
    landed_fresh(&settings, &root, &state, &line);
}

/// The store holds an unreadable draft state under each of `drafts`.
fn unreadable_drafts(
    root: &std::path::Path,
    state: &State,
    records: &[TurnRecord],
    drafts: &[String],
) {
    let directory = root.join("conversations").join(ID).join(".drafts");
    for draft in drafts {
        let opened = Write {
            draft: draft.clone(),
            ..earlier(state, records[..1].to_vec())
        };
        write(root, opened);
        fs::write(directory.join(format!("{draft}.state")), "not a state").unwrap();
    }
}

#[test]
fn an_unreadable_draft_of_the_turn_is_written_again_under_the_fresh_id() {
    let scratch = Scratch::new("resume-unreadable");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (state, messages) = queued(&settings, "the real prompt");
    let line = line_of(&settings, 1);
    let records = records_of(&messages).unwrap();
    unreadable_drafts(&root, &state, &records, std::slice::from_ref(&state.draft));
    // Ruling E15: a format refusal naming the turn's own draft gets the
    // fresh id, as `draft_mismatch` does.
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(2));
    assert_eq!(prompts(&root), ["the real prompt", "after"]);
    assert_eq!(rows(&root)[0]["draft"], json!(fresh_id(ID, &line)));
}

#[test]
fn an_unreadable_draft_under_both_ids_holds_the_turn_and_later_turns_land() {
    let scratch = Scratch::new("resume-unreadable-twice");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (state, messages) = queued(&settings, "the real prompt");
    let line = line_of(&settings, 1);
    let records = records_of(&messages).unwrap();
    let ids = [state.draft.clone(), fresh_id(ID, &line)];
    unreadable_drafts(&root, &state, &records, &ids);
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(1));
    assert_eq!(prompts(&root), ["after"]);
    let message =
        format!("Unsupported turn format in conversation '{ID}': a draft's state is unreadable");
    let code = "agent_conversation_format_unsupported";
    let expected = json!({"reason": "mismatch", "status": 400, "code": code, "message": message});
    assert_eq!(reason(&settings, 1), expected);
}

#[test]
fn a_draft_longer_than_the_turn_is_discarded_and_the_turn_written_under_the_fresh_id() {
    let scratch = Scratch::new("resume-longer");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (state, mut messages) = queued(&settings, "the real prompt");
    let line = line_of(&settings, 1);
    messages.push(Message::text(Role::Assistant, "more".into()));
    write(&root, earlier(&state, records_of(&messages).unwrap()));
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(2));
    assert_eq!(prompts(&root), ["the real prompt", "after"]);
    landed_fresh(&settings, &root, &state, &line);
}

#[test]
fn a_resumed_draft_that_stated_another_total_is_written_again_under_the_fresh_id() {
    let scratch = Scratch::new("resume-size");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let (state, messages) = queued(&settings, "the real prompt");
    let line = line_of(&settings, 1);
    let records = records_of(&messages).unwrap();
    let other = Write {
        turn_bytes: Some(turn_bytes(&records) + 1),
        ..earlier(&state, records[..1].to_vec())
    };
    write(&root, other);
    assert_eq!(turn(&settings, "after").unwrap().turn, Some(2));
    assert_eq!(prompts(&root), ["the real prompt", "after"]);
    landed_fresh(&settings, &root, &state, &line);
}
