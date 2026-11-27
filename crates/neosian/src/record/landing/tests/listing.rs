//! The listing's `recorded` and `size` lines written at each admitted
//! landing (DESIGN §40.54 item 19, the T1fd slice): cumulative over two
//! records turns with overlapping kinds and a multi-byte result, unchanged
//! by a seal resumed after its landing was documented, left out once after
//! a turn stored without its document, and never written by a store that
//! keeps no records. Expected bytes are the records' own UTF-8 sizes.

use std::fs;
use std::path::Path;

use serde_json::json;

use super::super::pack::turn_bytes;
use super::super::queue::Queue;
use super::land::{lander, turn};
use super::records::{enabled, plant, rows};
use super::{ID, record, settings};
use crate::conversation::message::from_json;
use crate::conversation::turn_records::records_of;
use crate::record::land::land;
use crate::record::payload::{Record, messages_of};
use crate::record::settings::Settings;
use crate::record::spool::Spool;
use crate::store::FileStore;
use crate::store::grammar::Actor;
use crate::testing::Scratch;

fn listed(root: &Path) -> String {
    let store = FileStore::open(root).unwrap();
    let path = format!("sessions/{ID}");
    store.read("user:me", &path).unwrap().unwrap().content
}

/// The Stop that closes `span`; its turn waits for a lander.
fn close(settings: &Settings, span: &mut Vec<Record>) {
    let actor = Actor::parse(&format!("claude-code:{ID}")).unwrap();
    let line = record(json!({"kind": "stop", "text": "built é"}));
    span.push(line.clone());
    let spool = Spool::new(&settings.spool);
    let landed = land(settings, &spool, ID, &actor, "Stop", &line).unwrap();
    assert!(landed.lander, "a store that keeps records only queues");
}

fn copy(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        fs::copy(entry.path(), to.join(entry.file_name())).unwrap();
    }
}

#[test]
fn records_landings_list_cumulative_kinds_and_bytes_and_a_resumed_seal_adds_nothing() {
    let scratch = Scratch::new("listing-cumulative");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let mut span = plant(&settings, 1, 3_000);
    close(&settings, &mut span);
    let pending = Queue::new(&settings.spool).pending_dir(ID);
    let saved = scratch.path().join("saved");
    copy(&pending, &saved);
    assert_eq!(lander(&settings).unwrap().turn, Some(1));
    let first = turn_bytes(&records_of(&messages_of(&span)).unwrap());
    assert!(first > 3_000 && rows(&root)[0]["bytes"] == first);
    let lines = format!(
        "- turns: 1\n- recorded: prompt, reply, tool_call, tool_result\n- size: {first} bytes\n"
    );
    assert!(listed(&root).contains(&lines), "{}", listed(&root));
    // The same queued turn again, as after a cut before its removal: the
    // probe finds the draft sealed and the landing adds nothing.
    copy(&saved, &pending);
    assert_eq!(lander(&settings).unwrap().turn, Some(1));
    assert!(listed(&root).contains(&lines), "{}", listed(&root));
    let mut span = plant(&settings, 0, 0);
    close(&settings, &mut span);
    assert_eq!(lander(&settings).unwrap().turn, Some(2));
    let second = turn_bytes(&records_of(&messages_of(&span)).unwrap());
    assert_eq!(second, "build it".len() as u64 + "built é".len() as u64);
    let total = first + second;
    let lines = format!(
        "- turns: 2\n- recorded: prompt, reply, tool_call, tool_result\n- size: {total} bytes\n"
    );
    assert!(listed(&root).contains(&lines), "{}", listed(&root));
}

#[test]
fn a_turn_stored_without_its_document_leaves_the_pair_out_once() {
    let scratch = Scratch::new("listing-gap");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    assert_eq!(turn(&settings, "one").unwrap().turn, Some(1));
    assert!(listed(&root).contains("- size: 11 bytes\n"));
    let message = from_json(&json!({"role": "user", "content": "two"})).unwrap();
    let store = FileStore::open(&root).unwrap();
    store.append_turn(ID, vec![message], None).unwrap();
    assert_eq!(turn(&settings, "three").unwrap().turn, Some(3));
    let gap = listed(&root);
    assert!(
        gap.contains("- turns: 3\n") && !gap.contains("- recorded:") && !gap.contains("- size:")
    );
    assert_eq!(turn(&settings, "four").unwrap().turn, Some(4));
    let again = "- turns: 4\n- recorded: prompt, reply\n- size: 13 bytes since turn 4\n";
    assert!(listed(&root).contains(again), "{}", listed(&root));
}

#[test]
fn a_store_without_records_lists_no_pair() {
    let scratch = Scratch::new("listing-plain");
    let settings = settings(scratch.path(), None);
    for prompt in ["one", "two"] {
        turn(&settings, prompt).unwrap();
    }
    let document = listed(&scratch.path().join("store"));
    assert!(
        document.ends_with("- last prompt: two\n- turns: 2\n"),
        "{document}"
    );
}
