//! Recall's views and parts over three origins of one turn: a FileStore
//! holding it whole (the legacy read), a FileStore keeping it as records,
//! and a store answering one record per page that logs each request.
//! The turn has prose with UTF-8 and JSON escapes, arguments over 64 KiB,
//! a result of about 200 KiB with head, middle and tail canaries, a
//! reasoning value, an orphan result and a call with no result.

mod fake;
mod parts;
mod whole;

use serde_json::{Value, json};

use self::fake::{Paged, Refuses};
use super::{Ask, Recalled, Selector, View, recall};
use crate::conversation::message::{Message, from_json};
use crate::conversation::turn_records::{TurnRecord, Write, records_of};
use crate::state::records::{Records, Source};
use crate::store::FileStore;
use crate::testing::Scratch;

const ID: &str = "c-recall";

fn result_text() -> String {
    let block = "lorem ipsum é \"quoted\" \\ back\n".repeat(3_300);
    format!("HEAD-CANARY ü\n{block}MIDDLE-CANARY\n{block}TAIL-CANARY")
}

fn message(value: Value) -> Message {
    from_json(&value).unwrap()
}

/// The turn's messages, in the order an agent wrote them.
fn messages() -> Vec<Message> {
    let blob = "A".repeat(150_000);
    vec![
        message(json!({"role": "user", "content": "Review café \"memory\"\nnow"})),
        message(json!({
            "role": "assistant", "content": "Reading it.",
            "reasoning": {"secret": "REASONING-CANARY"},
            "tool_calls": [{"id": "t1", "name": "Read", "arguments": {"path": "/a b", "blob": blob}}]
        })),
        message(json!({"role": "tool", "content": result_text(), "tool_call_id": "t1"})),
        message(json!({"role": "tool", "content": "orphan out", "tool_call_id": "lost"})),
        message(json!({"role": "assistant", "content": "  Done: 412 tests pass.  "})),
        message(json!({
            "role": "assistant", "content": null,
            "tool_calls": [{"id": "t2", "name": "Bash", "arguments": {"command": "ls"}}]
        })),
    ]
}

fn records() -> Vec<TurnRecord> {
    records_of(&messages()).unwrap()
}

/// A FileStore holding the turn whole, and one keeping it as records.
struct Stores {
    legacy: FileStore,
    kept: Source,
    _dirs: (Scratch, Scratch),
}

fn stores(name: &str) -> Stores {
    let (legacy_dir, kept_dir) = (
        Scratch::new(&format!("{name}-legacy")),
        Scratch::new(&format!("{name}-kept")),
    );
    let legacy = FileStore::open(legacy_dir.path()).unwrap();
    legacy.append_turn(ID, messages(), None).unwrap();
    let kept = FileStore::open(kept_dir.path()).unwrap();
    kept.enable_records().unwrap();
    let kept = Source::File(kept);
    let write = Write {
        draft: "d-1".into(),
        records: records(),
        seal: true,
        ..Write::default()
    };
    kept.keeper().unwrap().unwrap().write(ID, write).unwrap();
    Stores {
        legacy,
        kept,
        _dirs: (legacy_dir, kept_dir),
    }
}

fn ask(view: Option<View>, call: Option<&str>, seq: Option<i64>, part: Option<i64>) -> Ask {
    Ask::new(ID, 1, view, call, seq, part).unwrap()
}

const LABEL: &str = "Conversation 'c-recall'";

/// The same request through each origin; every answer must agree.
fn agreed(stores: &Stores, ask: &Ask) -> Result<Recalled, super::Corrective> {
    let legacy = recall(&stores.legacy, None, ask, LABEL).unwrap();
    let keeper = stores.kept.keeper().unwrap().unwrap();
    let kept = recall(stores.kept.state(), Some(&keeper), ask, LABEL).unwrap();
    let paged = Paged::new(records());
    let one = recall(&stores.legacy, Some(&paged), ask, LABEL).unwrap();
    assert_eq!(legacy, kept, "{ask:?}");
    assert_eq!(legacy, one, "{ask:?}");
    legacy
}

fn shown(stores: &Stores, ask: &Ask) -> String {
    agreed(stores, ask).unwrap().text
}

#[test]
fn every_view_reads_alike_from_records_and_whole_turns() {
    let stores = stores("recall-views");
    let light = shown(&stores, &ask(None, None, None, None));
    let lines: Vec<&str> = light.lines().collect();
    assert_eq!(
        lines[0],
        "Turn 1 (light: prompts and replies verbatim, one line per tool call):"
    );
    assert_eq!(lines[1], "USER: Review café \"memory\"");
    assert_eq!(lines[2], "now");
    assert_eq!(lines[3], "AGENT: Reading it.");
    assert!(lines[4].starts_with("TOOL Read({\"path\":\"/a b\",\"blob\":\"AAAA"));
    assert!(
        lines[4].contains(" …) → HEAD-CANARY ü lorem ipsum é"),
        "{}",
        lines[4]
    );
    let grouped = super::grouped(result_text().len() as u64);
    let end = format!("back TAIL-CANARY [{grouped} bytes; pass call=\"t1\" to read it]");
    assert!(lines[4].ends_with(&end), "{}", lines[4]);
    assert_eq!(
        lines[5],
        "TOOL tool → orphan out [10 bytes; pass seq=10 to read it]"
    );
    assert_eq!(lines[6], "AGENT: Done: 412 tests pass.");
    assert_eq!(lines[7], "TOOL Bash({\"command\":\"ls\"}) → ?");
    assert_eq!(lines.len(), 8);
    assert!(!light.contains("MIDDLE-CANARY") && !light.contains("REASONING-CANARY"));

    let prompts = shown(&stores, &ask(Some(View::Prompts), None, None, None));
    assert_eq!(
        prompts,
        "Turn 1 (prompts):\nUSER: Review café \"memory\"\nnow"
    );

    let calls = shown(&stores, &ask(Some(View::Calls), None, None, None));
    let blob = format!("\"blob\":\"{}\"}})\n", "A".repeat(150_000));
    assert!(calls.contains(&blob));
    let sized = format!("TOOL Read → [{grouped} bytes; pass call=\"t1\" to read it]");
    assert!(calls.contains(&sized));
    assert!(calls.contains("TOOL tool → [10 bytes; pass seq=10 to read it]"));
    assert!(!calls.contains("CANARY"));

    let full = shown(&stores, &ask(Some(View::Full), None, None, None));
    let opening = "Turn 1 (verbatim):\nUSER: Review café \"memory\"\nnow\nAGENT: Reading it.\n";
    assert!(full.starts_with(opening));
    assert!(full.contains("TOOL Read → HEAD-CANARY ü\n"));
    assert!(full.contains(&format!(
        " of {grouped} bytes omitted; pass call=\"t1\" to read it in parts] … "
    )));
    assert!(full.contains("TAIL-CANARY\nTOOL tool → orphan out\nAGENT: Done: 412 tests pass.\n"));
    assert!(full.ends_with("AGENT calls Bash({\"command\":\"ls\"})"));
    assert!(!full.contains("MIDDLE-CANARY") && !full.contains("REASONING-CANARY"));
}

#[test]
fn a_records_refusal_is_answered_never_read_again_whole() {
    let stores = stores("recall-refusal");
    let refusing = Refuses::new("agent_conversation_not_found", "gone");
    let answer = recall(
        &stores.legacy,
        Some(&refusing),
        &ask(None, None, None, None),
        LABEL,
    );
    let corrective = answer.unwrap().unwrap_err();
    assert_eq!(corrective.error, "[agent_conversation_not_found] gone");
}

#[test]
fn a_missing_or_redacted_turn_says_so_on_every_path() {
    let stores = stores("recall-missing");
    let nine = Ask::new(ID, 9, None, None, None, None).unwrap();
    let corrective = agreed(&stores, &nine).unwrap_err();
    assert_eq!(corrective.error, "Turn 9 does not exist");
    let hint = "Conversation 'c-recall' has turns 1-1.";
    assert_eq!(corrective.hint.as_deref(), Some(hint));
    let mut paged = Paged::new(Vec::new());
    paged.header.redacted = true;
    let full = ask(Some(View::Full), None, None, None);
    let answer = recall(&stores.legacy, Some(&paged), &full, LABEL);
    assert_eq!(answer.unwrap().unwrap().text, "Turn 1: [redacted]");
    stores
        .legacy
        .redact_turns(ID, None, Some(&[1]), None)
        .unwrap();
    let answer = recall(&stores.legacy, None, &ask(None, None, None, None), LABEL);
    assert_eq!(answer.unwrap().unwrap().text, "Turn 1: [redacted]");
    let call = recall(
        &stores.legacy,
        None,
        &ask(None, Some("t1"), None, None),
        LABEL,
    );
    assert_eq!(
        call.unwrap().unwrap_err().hint.as_deref(),
        Some("Its calls: none.")
    );
}

#[test]
fn a_request_the_rules_cannot_mean_is_refused_before_any_read() {
    let refused =
        |turn, view, call, seq, part| Ask::new(ID, turn, view, call, seq, part).unwrap_err();
    assert_eq!(
        refused(0, None, None, None, None).error,
        "Turn numbers start at 1; got 0"
    );
    let both = refused(1, None, Some("t1"), Some(2), None);
    assert_eq!(both.error, "Pass call or seq, not both");
    let negative = refused(1, None, None, Some(-1), None).error;
    assert_eq!(negative, "seq must be at least 0; got -1");
    let negative = refused(1, None, Some("t1"), None, Some(-2)).error;
    assert_eq!(negative, "part must be at least 0; got -2");
    assert_eq!(
        refused(1, None, None, None, Some(1)).error,
        "part needs call or seq"
    );
    let viewed = refused(1, Some(View::Full), Some("t1"), None, None).error;
    assert_eq!(
        viewed,
        "view shows a whole turn; call and seq open one part of it"
    );
    let asked = ask(None, None, Some(3), None);
    assert_eq!(asked.part, Some((Selector::Seq(3), 0)));
    assert_eq!(asked.view, View::Light);
}

#[test]
fn the_light_view_stops_within_its_ceiling_and_names_where_the_turn_goes_on() {
    let mut said = vec![message(json!({"role": "user", "content": "go"}))];
    for at in 0..12 {
        let text = format!("reply {at} {}", "w ".repeat(5_000));
        said.push(message(json!({"role": "assistant", "content": text})));
    }
    let paged = Paged::new(records_of(&said).unwrap());
    let scratch = Scratch::new("recall-ceiling");
    let store = FileStore::open(scratch.path()).unwrap();
    let light = recall(&store, Some(&paged), &ask(None, None, None, None), LABEL);
    let text = light.unwrap().unwrap().text;
    let mut lines: Vec<&str> = text.lines().collect();
    let end = lines.pop().unwrap();
    let body = lines[1..].join("\n");
    assert!(body.len() < super::light::LIGHT_BYTES, "{}", body.len());
    assert_eq!(lines.len(), 1 + 1 + 6);
    assert_eq!(
        end,
        "[the light view stops at 65,536 bytes; the turn goes on from record 7: \
         pass seq=7 to read that piece, or view=\"calls\" or view=\"full\" for the rest]"
    );
    let full = recall(
        &store,
        Some(&paged),
        &ask(Some(View::Full), None, None, None),
        LABEL,
    );
    assert!(full.unwrap().unwrap().text.contains("AGENT: reply 11 w"));
    let asked = paged.asked.borrow();
    assert!(asked.iter().all(|read| read.turn == Some(1)));
}
