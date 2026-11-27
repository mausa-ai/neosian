//! The hook's landing: closed turns land in order as separate turns, a
//! turn of another policy is held, Cursor closes once, an empty span
//! closes nothing. A Stop that leaves its turn to a lander is followed
//! here by the lander's landing of that conversation, in the foreground
//! (`lander`), and reported as the Stop reported it before T1e1d.

use std::fs;
use std::path::Path;

use serde_json::json;

use super::super::cap::Cap;
use super::super::queue::Queue;
use super::super::state::State;
use super::super::turn::land_queue;
use super::{ID, record, settings, spool};
use crate::conversation::Error;
use crate::conversation::message::text_of;
use crate::record::land::{Landed, here, land, settle};
use crate::record::settings::Settings;
use crate::record::spool::Spool;
use crate::store::FileStore;
use crate::store::grammar::Actor;
use crate::testing::Scratch;

fn event(settings: &Settings, name: &str, kind: &str, text: &str) -> Landed {
    let spool = Spool::new(&settings.spool);
    let actor = Actor::parse(&format!("{}:{ID}", settings.agent)).unwrap();
    let line = record(json!({"kind": kind, "text": text}));
    land(settings, &spool, ID, &actor, name, &line).unwrap()
}

/// A prompt and a stop; the stop's result, or its failure's text. A turn
/// the Stop leaves to a lander is landed as the lander lands it.
pub(super) fn turn(settings: &Settings, prompt: &str) -> Result<Landed, String> {
    event(settings, "UserPromptSubmit", "prompt", prompt);
    let spool = Spool::new(&settings.spool);
    let actor = Actor::parse(&format!("{}:{ID}", settings.agent)).unwrap();
    let stop = record(json!({"kind": "stop", "text": format!("{prompt} done")}));
    let landed = land(settings, &spool, ID, &actor, "Stop", &stop).map_err(|e| e.text())?;
    if !landed.lander {
        return Ok(landed);
    }
    lander(settings)
}

/// The conversation's queue landed as a lander lands it, and what that
/// tells the Stop that closed the newest turn.
pub(super) fn landed(settings: &Settings) -> Result<Landed, Error> {
    let queue = Queue::new(&settings.spool);
    let n = queue.next_n(ID).unwrap() - 1;
    let run = land_queue(&queue, ID, &here(settings, &Cap::NONE))?;
    settle(&queue, ID, n, run.unwrap_or_default())
}

/// `landed`, a failure as its text.
pub(super) fn lander(settings: &Settings) -> Result<Landed, String> {
    landed(settings).map_err(|e| e.text())
}

pub(super) fn prompts(root: &Path) -> Vec<String> {
    let store = FileStore::open(root).unwrap();
    let turns = store.read_turns(ID, 0, None).unwrap();
    turns
        .iter()
        .map(|t| text_of(&t.messages[0]).into_owned())
        .collect()
}

#[test]
fn turns_closed_by_failed_landings_land_in_order_as_separate_turns() {
    let scratch = Scratch::new("landing-order");
    let settings = settings(scratch.path(), None);
    let root = scratch.path().join("store");
    fs::write(&root, "not a directory").unwrap();
    for prompt in ["one", "two"] {
        assert!(turn(&settings, prompt).is_err());
    }
    let queue = Queue::new(&spool(scratch.path()));
    assert_eq!(queue.pending(ID).unwrap(), [1, 2]);
    fs::remove_file(&root).unwrap();
    let landed = turn(&settings, "three").unwrap();
    assert_eq!((landed.disposition, landed.turn), ("recorded", Some(3)));
    assert_eq!(
        landed.document.as_deref(),
        Some(&*format!("/user/sessions/{ID}"))
    );
    assert_eq!(prompts(&root), ["one", "two", "three"]);
    assert!(!queue.pending_dir(ID).exists());
    let store = FileStore::open(&root).unwrap();
    let document = store.read("user:me", &format!("sessions/{ID}")).unwrap();
    assert!(document.unwrap().content.contains("- turns: 3"));
}

#[test]
fn a_turn_of_another_policy_is_held_and_the_next_one_lands() {
    let scratch = Scratch::new("landing-policy");
    let root = scratch.path().join("store");
    fs::write(&root, "not a directory").unwrap();
    assert!(
        turn(
            &settings(scratch.path(), Some("old")),
            "under the old policy"
        )
        .is_err()
    );
    fs::remove_file(&root).unwrap();
    let current = settings(scratch.path(), Some("new"));
    assert_eq!(turn(&current, "under the new one").unwrap().turn, Some(1));
    assert_eq!(prompts(&root), ["under the new one"]);
    let queue = Queue::new(&spool(scratch.path()));
    let held = queue.held_dir(ID);
    assert_eq!(
        fs::read_to_string(held.join("1.reason")).unwrap(),
        "{\"reason\":\"policy_changed\"}\n"
    );
    let state = State::read(&held.join("1.state")).unwrap();
    assert_eq!(state.fingerprint.as_deref(), Some("old"));
    assert!(
        fs::read_to_string(held.join("1.jsonl"))
            .unwrap()
            .contains("under the old policy")
    );
    assert_eq!(turn(&current, "later").unwrap().turn, Some(2));
    assert!(held.join("1.jsonl").is_file());
}

#[test]
fn a_turn_for_another_destination_is_left_pending() {
    let scratch = Scratch::new("landing-elsewhere");
    let mut elsewhere = settings(scratch.path(), None);
    let blocked = scratch.path().join("blocked");
    fs::write(&blocked, "not a directory").unwrap();
    elsewhere.store = crate::remote::selection::Selection::File(blocked);
    assert!(turn(&elsewhere, "for the blocked root").is_err());
    let here = settings(scratch.path(), None);
    assert_eq!(turn(&here, "for this root").unwrap().turn, Some(1));
    assert_eq!(prompts(&scratch.path().join("store")), ["for this root"]);
    let queue = Queue::new(&spool(scratch.path()));
    assert_eq!(queue.pending(ID).unwrap(), [1]);
    assert!(!queue.held_dir(ID).exists());
}

#[test]
fn cursor_closes_one_turn_in_either_order() {
    for (index, order) in [["Stop", "AssistantResponse"], ["AssistantResponse", "Stop"]]
        .into_iter()
        .enumerate()
    {
        let scratch = Scratch::new(&format!("landing-cursor-{index}"));
        let mut settings = settings(scratch.path(), None);
        settings.agent = "cursor".into();
        event(&settings, "UserPromptSubmit", "prompt", "hello");
        let mut dispositions = Vec::new();
        for name in order {
            let spool = Spool::new(&settings.spool);
            let actor = Actor::parse(&format!("cursor:{ID}")).unwrap();
            let line = match name {
                "Stop" => record(json!({"kind": "stop", "text": "", "status": "completed"})),
                _ => record(json!({"kind": "assistant", "text": "done"})),
            };
            dispositions.push(land(&settings, &spool, ID, &actor, name, &line).unwrap());
        }
        let shown: Vec<_> = dispositions
            .iter()
            .map(|d| (d.disposition, d.turn))
            .collect();
        assert_eq!(shown, [("spooled", None), ("recorded", Some(1))]);
        assert_eq!(prompts(&scratch.path().join("store")), ["hello"]);
        assert!(!Queue::new(&settings.spool).pending_dir(ID).exists());
    }
}

#[test]
fn an_empty_span_closes_nothing() {
    let scratch = Scratch::new("landing-empty");
    let settings = settings(scratch.path(), None);
    assert_eq!(event(&settings, "Stop", "stop", "").disposition, "empty");
    assert!(!spool(scratch.path()).join("pending").exists());
    assert!(!spool(scratch.path()).join(format!("{ID}.jsonl")).exists());
}

#[test]
fn an_unreadable_span_fails_its_stop_is_held_as_it_was_and_the_next_one_lands() {
    let cases: [(&[u8], &str); 2] = [
        (b"7\n", " line 1 is not an object"),
        (
            b"{\"kind\": \"prompt\", \"text\": \"\xff\"}\n",
            "stream did not contain valid UTF-8",
        ),
    ];
    for (index, (planted, refused)) in cases.into_iter().enumerate() {
        let scratch = Scratch::new(&format!("landing-unreadable-{index}"));
        let settings = settings(scratch.path(), None);
        fs::create_dir_all(spool(scratch.path())).unwrap();
        fs::write(spool(scratch.path()).join(format!("{ID}.jsonl")), planted).unwrap();
        let actor = Actor::parse(&format!("claude-code:{ID}")).unwrap();
        let stop = record(json!({"kind": "stop", "text": "lost"}));
        let spool_of = Spool::new(&settings.spool);
        let failed = land(&settings, &spool_of, ID, &actor, "Stop", &stop).err();
        let failed = failed.expect("the Stop fails").text();
        assert!(failed.ends_with(refused), "{failed}");
        let held = Queue::new(&settings.spool).held_dir(ID);
        let kept = fs::read(held.join("1.jsonl")).unwrap();
        let at = kept.iter().position(|b| *b == b'\n').unwrap();
        let mut expected = planted.to_vec();
        expected.extend_from_slice(b"{\"kind\": \"stop\", \"text\": \"lost\"}\n");
        assert_eq!(&kept[at + 1..], &expected[..]);
        let reason = fs::read_to_string(held.join("1.reason")).unwrap();
        assert!(reason.starts_with("{\"reason\":\"unreadable\",\"message\":"));
        assert!(reason.contains(refused), "{reason}");
        assert_eq!(turn(&settings, "fresh").unwrap().turn, Some(1));
        assert_eq!(prompts(&scratch.path().join("store")), ["fresh"]);
    }
}

#[test]
fn a_document_that_cannot_be_written_never_sends_its_turn_again() {
    let scratch = Scratch::new("landing-document");
    let settings = settings(scratch.path(), None);
    let root = scratch.path().join("store");
    // The store takes turns; the scope's documents directory is a file.
    let documents = root.join("user%3Ame").join("documents");
    fs::create_dir_all(documents.parent().unwrap()).unwrap();
    fs::write(&documents, "not a directory").unwrap();
    for (prompt, stored) in [("one", vec!["one"]), ("two", vec!["one", "two"])] {
        // The document's own failure, not the emptied queue directory's.
        let failed = turn(&settings, prompt).err().expect("the Stop fails");
        assert_eq!(failed, "Not a directory (os error 20)");
        assert_eq!(prompts(&root), stored);
        assert!(Queue::new(&settings.spool).pending(ID).unwrap().is_empty());
    }
    fs::remove_file(&documents).unwrap();
    assert_eq!(turn(&settings, "three").unwrap().turn, Some(3));
    assert_eq!(prompts(&root), ["one", "two", "three"]);
}

#[test]
fn a_held_conversation_lock_leaves_the_closed_turn_pending() {
    let scratch = Scratch::new("landing-busy");
    let settings = settings(scratch.path(), None);
    let queue = Queue::new(&settings.spool);
    let lock = queue.try_lock(ID).unwrap().unwrap();
    assert_eq!(turn(&settings, "waits").unwrap().disposition, "pending");
    assert_eq!(queue.pending(ID).unwrap(), [1]);
    drop(lock);
    assert_eq!(turn(&settings, "then").unwrap().turn, Some(2));
    assert_eq!(prompts(&scratch.path().join("store")), ["waits", "then"]);
}
