//! T1b1a on a FileStore (DESIGN §40.54 items 4, 7, 9 and 15.1).

mod committing;
mod conflicts;
mod damaged;
mod files;
mod hit_bounds;
mod hit_scores;
mod hits;
mod lines;
mod memory;
mod numbers;
mod paging;
mod reading;
mod refusals;
mod restore_refusals;
mod restore_source;
mod restoring;
mod seal;
mod searching;
mod writing;

use serde_json::{Value, json};

use crate::conversation::message::{self, Message};
use crate::conversation::turn_records::{TurnRecord, Write, Written, records_of};
use crate::conversation::{Error, Turn};
use crate::limits;
use crate::pyfmt::dumps;
use crate::store::FileStore;
use crate::testing::Scratch;

const LIMITS: limits::Records = limits::Records {
    draft_seconds: 86_400,
    max_turn_bytes: 64 << 20,
    max_record_turn_bytes: 1 << 30,
};
const ACTOR: &str = "client:default/claude-code:t";

fn enabled(name: &str) -> (Scratch, FileStore) {
    let home = Scratch::new(name);
    let store = FileStore::open(home.path()).unwrap();
    assert!(store.enable_records().unwrap());
    (home, store)
}

fn messages(values: &[Value]) -> Vec<Message> {
    values
        .iter()
        .map(|value| message::from_json(value).unwrap())
        .collect()
}

/// Item 3.8's turn, its tool result 812,345 bytes in 13 records.
fn example() -> Vec<Message> {
    let result = format!(
        "{}{}",
        format!("{}\n", "a".repeat(999)).repeat(812),
        "a".repeat(345)
    );
    messages(&[
        json!({"role": "user", "content": "run the tests"}),
        json!({"role": "assistant", "content": null,
               "tool_calls": [{"id": "toolu_01", "name": "Bash", "arguments": {"command": "make test"}}]}),
        json!({"role": "tool", "content": result, "tool_call_id": "toolu_01"}),
        json!({"role": "assistant", "content": "All 412 tests pass."}),
    ])
}

/// Records packed into parts of at most `bytes` of record lines, as the
/// binary packs them.
fn parts(records: &[TurnRecord], bytes: usize) -> Vec<Vec<TurnRecord>> {
    let mut parts: Vec<Vec<TurnRecord>> = vec![Vec::new()];
    let mut used = 0;
    for record in records {
        let size = record.canonical(None).len();
        if used + size > bytes && !parts.last().unwrap().is_empty() {
            parts.push(Vec::new());
            used = 0;
        }
        used += size;
        parts.last_mut().unwrap().push(record.clone());
    }
    parts
}

fn part(
    store: &FileStore,
    conversation: &str,
    draft: &str,
    records: Vec<TurnRecord>,
    seal: bool,
) -> Result<Written, Error> {
    let write = Write {
        draft: draft.into(),
        records,
        seal,
        actor: Some(ACTOR.into()),
        ..Write::default()
    };
    store.write_records(conversation, write, &LIMITS)
}

/// A whole turn written in parts and sealed; the seal's answer.
fn written(store: &FileStore, conversation: &str, draft: &str, turn: &[Message]) -> Written {
    let records = records_of(turn).unwrap();
    let mut chunks = parts(&records, 983_040);
    let last = chunks.pop().unwrap();
    for chunk in chunks {
        part(store, conversation, draft, chunk, false).unwrap();
    }
    part(store, conversation, draft, last, true).unwrap()
}

fn spelled(messages: &[Message]) -> Vec<String> {
    messages
        .iter()
        .map(|m| dumps(&message::to_json(m), true))
        .collect()
}

fn turns(store: &FileStore, conversation: &str) -> Vec<Turn> {
    store.read_turns(conversation, 0, None).unwrap()
}

/// A conflict's reason, or the error's text.
fn reason(error: Error) -> String {
    match error {
        Error::Conversation(refused) if refused.code == "agent_conversation_conflict" => refused
            .details["reason"]
            .as_str()
            .unwrap_or_default()
            .to_string(),
        other => other.text(),
    }
}
