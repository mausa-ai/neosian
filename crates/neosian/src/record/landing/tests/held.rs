//! A turn refused for good (DESIGN §40.54 item 17.4, rulings E10 and E11
//! of the T1e1 plan): an older one is held while the Stop's own turn lands;
//! the Stop's own turn held fails with its own words and a hint naming
//! where it stays; a sessions document that fails at the same Stop does not
//! hide the hold. The refusal is a draft that is not the turn's under its
//! id and again under the fresh id, which 17.4 holds as `mismatch` (under
//! E8 one refusal held it `refused`).

use std::fs;

use serde_json::json;

use super::super::draft::{draft_id, fresh_id};
use super::super::held::hint;
use super::super::queue::{Queue, waiting};
use super::land::{damaged_document, landed, prompts, turn};
use super::records::{enabled, line_of, queued, stop, write};
use super::resume::reason;
use super::{ID, record, settings};
use crate::conversation::message::{Message, Role};
use crate::conversation::turn_records::{Write, records_of};
use crate::record::settings::Settings;
use crate::testing::Scratch;

/// An opening line written before the Stop, so the test knows the draft.
const OPENING: &str = "{\"kind\": \"span\", \"opened_at\": \"2026-11-26T12:00:00.000000Z\", \
                       \"nonce\": \"7b0c1f1e-2f3a-4b5c-8d6e-9f0a1b2c3d4e\"}";

/// The store holds a draft whose first prompt is not the turn's under the
/// id and under the fresh id of a turn whose opening line is `line`.
fn plant_other(root: &std::path::Path, line: &str) {
    for draft in [draft_id(ID, line), fresh_id(ID, line)] {
        let records = records_of(&[Message::text(Role::User, "a planted prompt".into())]).unwrap();
        let actor = Some(format!("claude-code:{ID}"));
        write(
            root,
            Write {
                draft,
                records,
                actor,
                ..Write::default()
            },
        );
    }
}

/// The Stop's own span, its opening line already written.
fn own_span(settings: &Settings, prompt: &str) -> Vec<crate::record::payload::Record> {
    let line = record(json!({"kind": "prompt", "text": prompt}));
    let text = format!("{OPENING}\n{}\n", serde_json::to_string(&line).unwrap());
    fs::create_dir_all(&settings.spool).unwrap();
    fs::write(settings.spool.join(format!("{ID}.jsonl")), text).unwrap();
    vec![line]
}

fn mismatch() -> serde_json::Value {
    json!({"reason": "mismatch", "status": 400, "code": "agent_conversation_conflict",
        "message": format!("Conversation conflict on '{ID}': draft_mismatch")})
}

#[test]
fn an_older_turn_refused_is_held_while_the_stop_s_own_turn_lands() {
    let scratch = Scratch::new("held-older");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    queued(&settings, "older");
    plant_other(&root, &line_of(&settings, 1));
    let landed = turn(&settings, "own").unwrap();
    assert_eq!((landed.disposition, landed.turn), ("recorded", Some(1)));
    assert_eq!(prompts(&root), ["own"]);
    assert_eq!(reason(&settings, 1), mismatch());
}

#[test]
fn a_stop_whose_own_turn_is_held_says_so_and_names_where_it_stays() {
    let scratch = Scratch::new("held-own");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let mut span = own_span(&settings, "own");
    plant_other(&root, OPENING);
    let failed = stop(&settings, &mut span).err();
    assert_eq!(
        failed.as_deref(),
        Some(&*format!(
            "the turn is held and will not be sent again: Conversation conflict on '{ID}': \
             draft_mismatch"
        ))
    );
    assert_eq!(reason(&settings, 1), mismatch());
    assert!(
        !root
            .join("conversations")
            .join(ID)
            .join("turns.jsonl")
            .exists()
    );
}

#[test]
fn a_document_that_fails_does_not_hide_the_stop_s_own_turn_held() {
    let scratch = Scratch::new("held-undocumented");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    queued(&settings, "older");
    // The store takes turns; the session's document is damaged.
    let (document, _) = damaged_document(&root);
    let mut span = own_span(&settings, "own");
    plant_other(&root, OPENING);
    let actor = crate::store::grammar::Actor::parse(&format!("claude-code:{ID}")).unwrap();
    let line = record(json!({"kind": "stop", "text": "built"}));
    span.push(line.clone());
    let spool = crate::record::spool::Spool::new(&settings.spool);
    let queued = crate::record::land::land(&settings, &spool, ID, &actor, "Stop", &line);
    assert!(queued.unwrap().lander);
    let error = landed(&settings).err().expect("the landing fails");
    let text = error.text();
    assert!(
        text.starts_with("the turn is held and will not be sent again: "),
        "{text}"
    );
    let held = Queue::new(&settings.spool).held_dir(ID);
    let expected = format!(
        "the record never blocks the agent; the turn stays in {} with its reason in {}, \
         and nothing sends it again by itself",
        held.join("2.jsonl").display(),
        held.join("2.reason").display()
    );
    assert_eq!(hint(&error), Some(expected));
    assert_eq!(reason(&settings, 2), mismatch());
    assert_eq!(prompts(&root), ["older"]);
    fs::remove_file(&document).unwrap();
    assert_eq!(turn(&settings, "later").unwrap().turn, Some(2));
}

#[test]
fn a_tree_whose_only_closed_turn_is_held_has_turns_that_have_not_landed() {
    // Ruling E17: logout counts held turns as well as pending ones.
    let scratch = Scratch::new("held-waiting");
    let spool = scratch.path().join("spool");
    assert!(!waiting(&spool));
    let held = Queue::new(&spool).held_dir(ID);
    fs::create_dir_all(&held).unwrap();
    fs::write(held.join("1.reason"), "{}\n").unwrap();
    assert!(!waiting(&spool), "a reason alone is no turn");
    fs::write(held.join("1.jsonl"), "{}\n").unwrap();
    assert!(waiting(&spool));
}
