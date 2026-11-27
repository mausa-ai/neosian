//! A stored turn records cannot carry (a call id over 1,024 bytes) is
//! shown whole as released, after the fixed line, on a store without
//! records and on one whose `read_records` refuses it, that turn alone read;
//! `call` answers as released; any other refusal, a damaged record's under
//! the same code included, and a missing turn, stay refusals.

use serde_json::json;

use super::fake::Refuses;
use super::{LABEL, ask, message, stores};
use crate::conversation::recall::{Ask, View, recall};
use crate::state::State;
use crate::state::records::{Records, Source};
use crate::store::FileStore;
use crate::testing::Scratch;

const ID: &str = "c-recall";

fn long_id() -> String {
    "c".repeat(1_025)
}

fn append(store: &FileStore) {
    let said = vec![
        message(json!({"role": "user", "content": "look"})),
        message(json!({
            "role": "assistant", "content": null,
            "tool_calls": [{"id": long_id(), "name": "Read", "arguments": {"path": "/x"}}]
        })),
        message(json!({"role": "tool", "content": "ok", "tool_call_id": long_id()})),
        message(json!({"role": "assistant", "content": "done"})),
    ];
    store.append_turn(ID, said, None).unwrap();
}

#[test]
fn a_turn_records_cannot_carry_is_shown_whole_as_released() {
    let (plain, kept) = (
        Scratch::new("recall-whole"),
        Scratch::new("recall-whole-kept"),
    );
    let legacy = FileStore::open(plain.path()).unwrap();
    append(&legacy);
    let enabled = FileStore::open(kept.path()).unwrap();
    enabled.enable_records().unwrap();
    append(&enabled);
    let enabled = Source::File(enabled);
    let keeper = enabled.keeper().unwrap().unwrap();
    let whole = "[this turn cannot be shown in views; it is shown whole, as recorded]\n\
                 Turn 1 (verbatim):\nUSER: look\nAGENT calls Read({\"path\":\"/x\"})\n\
                 TOOL Read → ok\nAGENT: done";
    let call = format!(
        "Turn 1, call {} (verbatim):\nAGENT calls Read({{\"path\":\"/x\"}})\nTOOL Read → ok",
        long_id()
    );
    let origins: [(&dyn State, Option<&dyn Records>); 2] =
        [(&legacy, None), (enabled.state(), Some(&keeper))];
    for (store, records) in origins {
        let shown = |ask: &Ask| recall(store, records, ask, LABEL).unwrap();
        for view in [None, Some(View::Prompts), Some(View::Full)] {
            let answer = shown(&ask(view, None, None, None)).unwrap();
            assert_eq!(answer.text, whole);
        }
        assert_eq!(
            shown(&ask(None, Some(&long_id()), None, None))
                .unwrap()
                .text,
            call
        );
        let nine = Ask::new(ID, 9, None, None, None, None).unwrap();
        assert_eq!(shown(&nine).unwrap_err().error, "Turn 9 does not exist");
        let seq = shown(&ask(None, None, Some(0), None)).unwrap_err();
        assert_eq!(seq.error, "Turn 1 has no parts to open");
    }
    // Only the refusal of the asked turn because records cannot carry it
    // reads that turn whole; a damaged record's refusal under the same
    // code, and one naming another turn, stay refusals.
    let stores = stores("recall-whole-refusal");
    let light = ask(None, None, None, None);
    let expected = recall(&stores.legacy, None, &light, LABEL).unwrap();
    let code = "agent_conversation_format_unsupported";
    let said = |reason: &str| format!("Unsupported turn format in conversation '{ID}': {reason}");
    let carry = "message 1 has a call id longer than 1024 bytes, which records cannot carry";
    let unconvertible = Refuses::new(code, &said(&format!("turn 1: {carry}")));
    let fallen = recall(&stores.legacy, Some(&unconvertible), &light, LABEL).unwrap();
    assert_eq!(fallen, expected);
    for reason in [
        "turn 1: its records file is missing",
        &format!("turn 2: {carry}"),
    ] {
        let refusing = Refuses::new(code, &said(reason));
        let refused = recall(&stores.legacy, Some(&refusing), &light, LABEL).unwrap();
        assert_eq!(
            refused.unwrap_err().error,
            format!("[{code}] {}", said(reason))
        );
    }
    let other = Refuses::new("agent_conversation_not_found", "gone");
    let refused = recall(&stores.legacy, Some(&other), &light, LABEL).unwrap();
    assert_eq!(
        refused.unwrap_err().error,
        "[agent_conversation_not_found] gone"
    );
}
