//! Turns landed as records on a FileStore that keeps them (DESIGN §40.54
//! items 7, 9 and 17.3): read back as their messages at 1 MiB and 16 MiB,
//! a store without records given one `append_turn`, a refused part, and a
//! turn records cannot carry. The helpers serve `resume.rs` and `held.rs`.

use std::fs;
use std::path::Path;

use serde_json::{Value, json};

use super::super::pack::{Draft, turn_bytes};
use super::super::queue::Queue;
use super::super::seal::{Sent, send};
use super::super::sink::Sink;
use super::super::state::{Reason, State};
use super::land::turn;
use super::{ID, record, settings, state};
use crate::conversation::message::{self, Message, Role};
use crate::conversation::turn_records::{Form, Kind, TurnRecord, Write, records_of};
use crate::limits;
use crate::pyfmt::dumps;
use crate::record::land::{Landed, land};
use crate::record::payload::{Record, messages_of};
use crate::record::settings::Settings;
use crate::record::spool::{Spool, parse};
use crate::remote::selection::Selection;
use crate::store::FileStore;
use crate::store::grammar::Actor;
use crate::testing::Scratch;

pub(super) fn spelled(messages: &[Message]) -> Vec<String> {
    let spell = |m: &Message| dumps(&message::to_json(m), true);
    messages.iter().map(spell).collect()
}

pub(super) fn enabled(base: &Path) -> std::path::PathBuf {
    let root = base.join("store");
    assert!(FileStore::open(&root).unwrap().enable_records().unwrap());
    root
}

/// A span of a prompt and `rounds` tool rounds whose results hold
/// `result` characters each, written as the spool keeps it.
pub(super) fn plant(settings: &Settings, rounds: usize, result: usize) -> Vec<Record> {
    let output = "make: built é 中 \"ok\"\n".repeat(result / 20 + 1);
    let output: String = output.chars().take(result).collect();
    let mut lines = vec![record(json!({"kind": "prompt", "text": "build it"}))];
    for round in 0..rounds {
        lines.push(record(
            json!({"kind": "tool", "id": format!("call-{round}"),
            "name": "Bash", "input": {"command": "make"}, "response": output}),
        ));
    }
    let text: String = lines
        .iter()
        .map(|line| serde_json::to_string(line).unwrap() + "\n")
        .collect();
    fs::create_dir_all(&settings.spool).unwrap();
    fs::write(settings.spool.join(format!("{ID}.jsonl")), text).unwrap();
    lines
}

pub(super) fn stop(settings: &Settings, span: &mut Vec<Record>) -> Result<Landed, String> {
    let actor = Actor::parse(&format!("claude-code:{ID}")).unwrap();
    let line = record(json!({"kind": "stop", "text": "built"}));
    span.push(line.clone());
    let spool = Spool::new(&settings.spool);
    land(settings, &spool, ID, &actor, "Stop", &line).map_err(|e| e.text())
}

pub(super) fn rows(root: &Path) -> Vec<Value> {
    let file = root.join("conversations").join(ID).join("turns.jsonl");
    let text = fs::read_to_string(file).unwrap();
    text.lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

#[test]
fn a_1_mib_and_a_16_mib_turn_land_as_records_and_read_back_as_their_messages() {
    for rounds in [1, 16] {
        let scratch = Scratch::new(&format!("records-size-{rounds}"));
        let settings = settings(scratch.path(), None);
        let root = enabled(scratch.path());
        let mut span = plant(&settings, rounds, 1 << 20);
        let landed = stop(&settings, &mut span).unwrap();
        assert_eq!((landed.disposition, landed.turn), ("recorded", Some(1)));
        let messages = messages_of(&span);
        let store = FileStore::open(&root).unwrap();
        let turns = store.read_turns(ID, 0, None).unwrap();
        assert_eq!(turns.len(), 1);
        assert_eq!(spelled(&turns[0].messages), spelled(&messages));
        assert_eq!(
            turns[0].actor.as_deref(),
            Some(&*format!("claude-code:{ID}"))
        );
        let records = records_of(&messages).unwrap();
        let [header] = &rows(&root)[..] else {
            panic!("one row")
        };
        assert_eq!(header["neosian_format"], 2);
        assert_eq!(header["records"], records.len());
        assert_eq!(header["bytes"], turn_bytes(&records));
        assert!(turn_bytes(&records) > (rounds as u64) << 20);
        assert!(!Queue::new(&settings.spool).pending_dir(ID).exists());
        let document = store.read("user:me", &format!("sessions/{ID}")).unwrap();
        assert!(document.unwrap().content.contains("- turns: 1"));
    }
}

#[test]
fn a_store_without_records_receives_one_append_turn() {
    let scratch = Scratch::new("records-absent");
    let settings = settings(scratch.path(), None);
    let mut span = plant(&settings, 2, 1_000);
    assert_eq!(stop(&settings, &mut span).unwrap().turn, Some(1));
    let root = scratch.path().join("store");
    let [row] = &rows(&root)[..] else {
        panic!("one row")
    };
    assert_eq!(row["neosian_format"], 1);
    assert_eq!(row["messages"].as_array().unwrap().len(), 6);
    assert!(!root.join("conversations").join(ID).join(".drafts").exists());
}

/// A turn closed while the test holds its lock, with its state.
pub(super) fn queued(settings: &Settings, prompt: &str) -> (State, Vec<Message>) {
    let queue = Queue::new(&settings.spool);
    let lock = queue.try_lock(ID).unwrap().unwrap();
    assert_eq!(turn(settings, prompt).unwrap().disposition, "pending");
    drop(lock);
    let directory = queue.pending_dir(ID);
    let state = State::read(&directory.join("1.state")).unwrap();
    let file = directory.join("1.jsonl");
    let spool = parse(&fs::read_to_string(&file).unwrap(), &file).unwrap();
    (state, messages_of(&spool))
}

/// The opening line of queued turn `n`.
pub(super) fn line_of(settings: &Settings, n: u64) -> String {
    let file = Queue::new(&settings.spool)
        .pending_dir(ID)
        .join(format!("{n}.jsonl"));
    let text = fs::read_to_string(file).unwrap();
    text.split('\n').next().unwrap().to_string()
}

pub(super) fn write(root: &Path, write: Write) {
    let store = FileStore::open(root).unwrap();
    let limits = limits::Records::load().unwrap();
    store.write_records(ID, write, &limits).unwrap();
}

pub(super) fn tiny(seq: u64, kind: Kind) -> TurnRecord {
    TurnRecord {
        seq,
        message: seq,
        role: Role::User,
        kind,
        content: Some(Form::Text),
        block: None,
        call: None,
        call_id: None,
        name: None,
        chunk: 0,
        size: 1,
        text: "x".into(),
    }
}

#[test]
fn a_refused_part_discards_the_draft_and_keeps_the_last_confirmed_next_seq() {
    let scratch = Scratch::new("records-refused");
    let root = enabled(scratch.path());
    let line = "{\"kind\": \"span\"}";
    let mut state = state(line, 1);
    let file = scratch.path().join("1.state");
    state.write(&file).unwrap();
    // Three parts; the last record is a reply in a user's message.
    let mut records: Vec<TurnRecord> = (0..4_096).map(|seq| tiny(seq, Kind::Prompt)).collect();
    records.push(tiny(4_096, Kind::Reply));
    let sink = Sink::open(&Selection::File(root.clone())).unwrap();
    let keeper = sink.records().unwrap().unwrap();
    let draft = Draft {
        conversation: ID,
        draft: &state.draft.clone(),
        actor: "claude-code:x",
        turn_bytes: turn_bytes(&records),
    };
    let Sent::Held(reason) = send(&keeper, &draft, records, &mut state, &file).unwrap() else {
        panic!("held")
    };
    let message =
        "record 4096: a reply record of this shape cannot belong to a message of role user";
    assert_eq!(
        reason,
        Reason::refused(Some(400), Some("value_error".into()), message.into())
    );
    assert_eq!(State::read(&file).unwrap().next_seq, 4_096);
    let probe = keeper.write(ID, draft.probe()).unwrap();
    let super::super::answer::Answer::Written(probe) = probe else {
        panic!("answered")
    };
    assert_eq!(probe.state.as_str(), "none");
}

#[test]
fn a_turn_whose_records_cannot_carry_a_call_id_lands_whole() {
    let scratch = Scratch::new("records-long-id");
    let settings = settings(scratch.path(), None);
    let root = enabled(scratch.path());
    let mut span = plant(&settings, 0, 0);
    span.push(record(json!({"kind": "tool", "id": "i".repeat(1_025),
        "name": "Bash", "input": {}, "response": "ok"})));
    let text: String = span
        .iter()
        .map(|line| serde_json::to_string(line).unwrap() + "\n")
        .collect();
    fs::write(settings.spool.join(format!("{ID}.jsonl")), text).unwrap();
    assert_eq!(stop(&settings, &mut span).unwrap().turn, Some(1));
    let [row] = &rows(&root)[..] else {
        panic!("one row")
    };
    assert_eq!(row["neosian_format"], 1);
    let store = FileStore::open(&root).unwrap();
    let turns = store.read_turns(ID, 0, None).unwrap();
    assert_eq!(spelled(&turns[0].messages), spelled(&messages_of(&span)));
}
