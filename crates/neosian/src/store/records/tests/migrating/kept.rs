//! Rows a migration leaves whole (§40.54 item 21, T1b2c3): a turn whose
//! records could not give its stored line back byte for byte keeps its
//! bytes and its place, and the answer counts it by reason. A line holding
//! a key the turn or one of its messages does not carry, or an id records
//! cannot carry, is `content`; another spelling of the same turn (spaces,
//! raw non-ASCII, a carriage return, default keys left out, no final
//! newline) is `spelling`; a turn over
//! `NEOSIAN_MAX_RECORD_TURN_BYTES` is `size`; a number whose records file
//! is already there is `number`.

use std::fs;

use serde_json::{Value, json};

use super::super::{LIMITS, enabled, messages};
use super::{Kept, Migrated, log, whole};
use crate::conversation::Turn;
use crate::conversation::message::Message;
use crate::conversation::rows::render_turn;
use crate::conversation::rows_header::{Row, parse_row};
use crate::limits;
use crate::pyfmt::{dumps, dumps_spaced};
use crate::store::time::Stamp;

fn row(turn: u64, messages: Vec<Message>) -> String {
    render_turn(&Turn {
        conversation_id: "c-1".into(),
        turn,
        messages,
        created_at: Stamp::parse("2026-10-02T09:15:00Z").unwrap(),
        actor: Some("cli:t".into()),
        redacted: false,
    })
}

fn user(text: &str) -> Vec<Message> {
    messages(&[json!({"role": "user", "content": text})])
}

fn value(line: &str) -> Value {
    serde_json::from_str(line.trim_end()).unwrap()
}

#[test]
fn rows_that_would_not_come_back_byte_for_byte_stay_whole_in_place() {
    let (_home, store) = enabled("records-migrate-kept");
    let mut unknown = value(&row(2, user("two")));
    unknown["note"] = json!("written by hand");
    let long_id = json!({"role": "tool", "content": "x", "tool_call_id": "i".repeat(1_025)});
    // The same turn with its messages' default keys left out, and a turn
    // whose message holds a key no message carries.
    let bare = "{\"neosian_format\":1,\"turn\":9,\"created_at\":\"2026-10-02T09:15:00Z\",\
                \"messages\":[{\"role\":\"user\",\"content\":\"bare\"}],\"actor\":\"cli:t\"}\n";
    let mut nested = value(&row(10, user("ten")));
    nested["messages"][0]["note"] = json!("written by hand");
    let lines = [
        row(1, user("one")),
        format!("{}\n", dumps(&unknown, true)),
        format!(
            "{}\n",
            dumps_spaced(&value(&row(3, user("three"))), true, false)
        ),
        format!("{}\n", dumps(&value(&row(4, user("h\u{e9}"))), false)),
        row(5, user("five")).replace('\n', "\r\n"),
        row(6, messages(&[long_id])),
        row(7, user(&"w ".repeat(40_000))),
        row(8, user("eight")),
        bare.to_string(),
        format!("{}\n", dumps(&nested, true)),
        row(11, user("eleven")).trim_end().to_string(),
    ];
    let directory = store.conversation_dir("c-1").unwrap();
    fs::create_dir_all(directory.join("records")).unwrap();
    fs::write(log(&store, "c-1"), lines.concat()).unwrap();
    fs::write(directory.join("records/8.jsonl"), "another turn's\n").unwrap();
    let before = whole(&store, "c-1");
    assert_eq!(before.len(), 11);

    let limits = limits::Records {
        max_record_turn_bytes: 65_536,
        ..LIMITS
    };
    let mut found = Migrated {
        conversation_id: "c-1".into(),
        migrated: 1,
        records: 0,
        skeletons: 0,
        kept: Kept {
            spelling: 5,
            content: 3,
            size: 1,
            number: 1,
        },
    };
    assert_eq!(store.migrate_records("c-1", &limits).unwrap(), found);
    let after = fs::read_to_string(log(&store, "c-1")).unwrap();
    let (header, rest) = after.split_once('\n').unwrap();
    assert!(matches!(
        parse_row(header, 1, "c-1", true).unwrap(),
        Row::Records(_)
    ));
    assert_eq!(rest, lines[1..].concat(), "every kept row, byte for byte");
    assert_eq!(
        fs::read_to_string(directory.join("records/8.jsonl")).unwrap(),
        "another turn's\n"
    );
    assert_eq!(whole(&store, "c-1"), before);

    (found.migrated, found.records) = (0, 1);
    assert_eq!(store.migrate_records("c-1", &limits).unwrap(), found);
    assert_eq!(fs::read_to_string(log(&store, "c-1")).unwrap(), after);
    // Raising the bound migrates the turn it kept; the others stay.
    (found.migrated, found.kept.size) = (1, 0);
    assert_eq!(store.migrate_records("c-1", &LIMITS).unwrap(), found);
    assert_eq!(whole(&store, "c-1"), before);

    // A fold gives the first log back, every kept row with its own bytes.
    let mut listing = store.record_places().unwrap();
    let folded = store.fold_records("c-1", &mut listing, false, &LIMITS);
    let folded = folded.unwrap();
    assert_eq!((folded.folded, folded.whole), (2, 9));
    assert_eq!(
        fs::read_to_string(log(&store, "c-1")).unwrap(),
        lines.concat()
    );
    assert_eq!(whole(&store, "c-1"), before);
}
