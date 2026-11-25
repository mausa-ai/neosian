use serde_json::{Value, json};

use super::*;
use crate::conversation::message;
use crate::conversation::turn_records::ReadPosition;
use crate::store::FileStore;
use crate::testing::Scratch;

const SESSION: &str = "c-pages";
const CAP: u64 = 8 << 20;

fn body(value: Value) -> Payload {
    value.as_object().expect("an object").clone()
}

fn refused(request: Value) -> String {
    let payload = body(request);
    let fail = selection(&payload, CAP).map(|_| ()).expect_err("refused");
    assert_eq!((fail.status.as_u16(), fail.code), (400, "value_error"));
    fail.message
}

#[test]
fn an_absent_field_takes_its_default_and_page_bytes_is_capped() {
    let payload = body(json!({"conversation_id": SESSION}));
    let (id, read) = selection(&payload, CAP).unwrap();
    assert_eq!(id, SESSION);
    assert_eq!((read.turn, read.after, read.through), (None, 0, None));
    assert_eq!((read.seq, read.piece), (None, false));
    assert!(read.kinds.is_none() && read.heads.is_empty());
    assert_eq!((read.page_bytes, read.continuation), (983_040, None));
    let asked = json!({
        "conversation_id": SESSION, "turn": 4, "seq": 2, "piece": true,
        "kinds": ["reply", "prompt", "reply"], "heads": {"tool_result": 32768, "reply": 1},
        "page_bytes": u64::MAX, "continuation": "a".repeat(4096),
    });
    let (_, read) = selection(&body(asked), 5).unwrap();
    assert_eq!((read.turn, read.seq, read.piece), (Some(4), Some(2), true));
    assert_eq!(read.kinds, Some(vec![Kind::Reply, Kind::Prompt]));
    assert_eq!(
        read.heads,
        [(Kind::ToolResult, 32_768), (Kind::Reply, 1)],
        "in the body's order"
    );
    assert_eq!(read.page_bytes, 5, "served at most the server's page");
    let (_, small) = selection(&body(json!({"conversation_id": "c", "page_bytes": 1})), 5).unwrap();
    assert_eq!(small.page_bytes, 1);
    let empty = json!({"conversation_id": "c", "kinds": [], "heads": {}});
    let (_, read) = selection(&body(empty), CAP).unwrap();
    assert_eq!(read.kinds, Some(Vec::new()));
}

#[test]
fn every_rule_of_the_table_refuses_by_name() {
    let with = |more: Value| {
        let mut request = body(json!({"conversation_id": SESSION}));
        request.extend(body(more));
        refused(Value::Object(request))
    };
    assert_eq!(refused(json!({})), "missing parameter: 'conversation_id'");
    assert_eq!(
        refused(json!({"conversation_id": null})),
        "parameter 'conversation_id' must be a string"
    );
    assert_eq!(with(json!({"limit": 1})), "unknown parameter: 'limit'");
    let excluded = "turn excludes after and through";
    assert_eq!(with(json!({"turn": 1, "after": 0})), excluded);
    assert_eq!(with(json!({"turn": 1, "after": 2})), excluded);
    assert_eq!(with(json!({"turn": 1, "through": 2})), excluded);
    let least = "turn and through are at least 1";
    assert_eq!(with(json!({"turn": 0})), least);
    assert_eq!(with(json!({"through": 0})), least);
    assert_eq!(with(json!({"seq": 0})), "seq requires turn");
    assert_eq!(with(json!({"piece": true})), "piece requires turn and seq");
    // A named field counts even at its default.
    assert_eq!(with(json!({"piece": false})), "piece requires turn and seq");
    assert_eq!(
        with(json!({"turn": 1, "piece": false})),
        "piece requires turn and seq"
    );
    assert_eq!(
        with(json!({"turn": 1, "piece": true})),
        "piece requires turn and seq"
    );
    for name in ["turn", "after", "through", "seq", "page_bytes"] {
        for value in [json!(-1), json!(1.5), json!("1"), json!(null), json!(true)] {
            assert_eq!(
                with(json!({name: value})),
                format!("parameter '{name}' must be a non-negative integer")
            );
        }
    }
    assert_eq!(
        with(json!({"piece": null})),
        "parameter 'piece' must be a boolean"
    );
    assert_eq!(
        with(json!({"kinds": "prompt"})),
        "parameter 'kinds' must be a list"
    );
    assert_eq!(
        with(json!({"kinds": null})),
        "parameter 'kinds' must be a list"
    );
    assert_eq!(
        with(json!({"kinds": ["prompt", 7]})),
        "parameter 'kinds' must be a list of kinds"
    );
    assert_eq!(
        with(json!({"kinds": ["prompts"]})),
        "unknown record kind \"prompts\""
    );
    assert_eq!(
        with(json!({"heads": ["reply"]})),
        "parameter 'heads' must be an object"
    );
    assert_eq!(
        with(json!({"heads": {"answer": 5}})),
        "unknown record kind \"answer\""
    );
    for bytes in [json!(0), json!(32_769), json!("9"), json!(null), json!(2.5)] {
        assert_eq!(
            with(json!({"heads": {"tool_result": bytes}})),
            "heads \"tool_result\" must be 1 to 32768"
        );
    }
    assert_eq!(with(json!({"page_bytes": 0})), "page_bytes must be >= 1");
    let long = "continuation must contain 1..4096 bytes";
    assert_eq!(with(json!({"continuation": ""})), long);
    assert_eq!(with(json!({"continuation": "a".repeat(4097)})), long);
    assert_eq!(
        with(json!({"continuation": null})),
        "parameter 'continuation' must be a string"
    );
}

fn store(name: &str, turns: &[Vec<Value>]) -> (Scratch, FileStore) {
    let home = Scratch::new(name);
    let store = FileStore::open(home.path()).unwrap();
    assert!(store.enable_records().unwrap());
    for messages in turns {
        let messages = messages
            .iter()
            .map(|value| message::from_json(value).unwrap())
            .collect();
        store.append_turn(SESSION, messages, None).unwrap();
    }
    (home, store)
}

/// The answer assembled in memory from the store's own items.
fn assembled(store: &FileStore, read: &RecordRead) -> (String, Option<ReadPosition>) {
    let (mut turns, mut records) = (Vec::new(), Vec::new());
    let next = store
        .read_records(SESSION, read, &mut |item| {
            match item {
                ReadItem::Header(_) => turns.push(item.to_json()),
                ReadItem::Record { .. } => records.push(item.to_json()),
            }
            Ok(())
        })
        .unwrap();
    let token = next.map(|next| next.encode(&read.fingerprint(SESSION)));
    let whole = json!({"turns": turns, "records": records, "continuation": token});
    (dumps(&whole, false), next)
}

fn paged(store: &FileStore, read: &RecordRead) -> (Vec<bool>, u64, Option<String>, String) {
    let mut writer = Writer::alone();
    page(store, SESSION, read, &mut writer).unwrap();
    let (disk, held) = (writer.on_disk(), writer.in_memory_bytes());
    let (length, bytes) = writer.sent();
    (disk, held, length, String::from_utf8(bytes).unwrap())
}

#[test]
fn a_page_is_turns_then_records_then_continuation_as_assembled_in_memory() {
    let turn = |n: usize| {
        vec![
            json!({"role": "user", "content": format!("ask {n}")}),
            json!({"role": "assistant", "content": "é\n".repeat(40 * n)}),
        ]
    };
    let (_home, store) = store("record-pages-order", &[turn(1), turn(2), turn(3)]);
    let views = [
        RecordRead::default(),
        RecordRead {
            kinds: Some(Vec::new()),
            ..RecordRead::default()
        },
        RecordRead {
            kinds: Some(vec![Kind::Prompt]),
            page_bytes: 1,
            ..RecordRead::default()
        },
        RecordRead {
            after: 1,
            page_bytes: 600,
            ..RecordRead::default()
        },
        RecordRead {
            after: 9,
            ..RecordRead::default()
        },
    ];
    for read in views {
        let (whole, next) = assembled(&store, &read);
        let (disk, _, length, bytes) = paged(&store, &read);
        assert_eq!(bytes, whole);
        assert_eq!(length, Some(whole.len().to_string()));
        assert_eq!(disk, [false, false], "a small page stays in memory");
        let turns = bytes.find("\"turns\":[").unwrap();
        let records = bytes.find("],\"records\":[").unwrap();
        let continuation = bytes.rfind("],\"continuation\":").unwrap();
        assert!(turns == 1 && turns < records && records < continuation);
        assert_eq!(next.is_some(), !bytes.ends_with("\"continuation\":null}"));
    }
    let empty = "{\"turns\":[],\"records\":[],\"continuation\":null}";
    assert_eq!(
        paged(
            &store,
            &RecordRead {
                after: 9,
                ..RecordRead::default()
            }
        )
        .3,
        empty
    );
}

#[test]
fn a_page_past_1_mib_keeps_its_records_in_the_second_spool_on_disk() {
    let long = "all work and no play\n".repeat(160_000);
    assert!(long.len() > 3 << 20);
    let turns = [
        vec![json!({"role": "user", "content": "first"})],
        vec![json!({"role": "tool", "content": long, "tool_call_id": "t1"})],
        vec![json!({"role": "user", "content": "last"})],
    ];
    let (_home, store) = store("record-pages-spooled", &turns);
    let read = RecordRead {
        page_bytes: CAP,
        ..RecordRead::default()
    };
    let (whole, next) = assembled(&store, &read);
    assert!(next.is_none() && whole.len() > 3 << 20);
    let (disk, held, length, bytes) = paged(&store, &read);
    assert_eq!(disk, [false, true], "headers in memory, records on disk");
    assert!(held < 4_096, "the page is not held: {held} bytes in memory");
    assert_eq!(length, Some(whole.len().to_string()));
    assert!(bytes == whole);
    // At the default the same turn pages under the in-memory spool.
    let (disk, held, _, bytes) = paged(&store, &RecordRead::default());
    assert_eq!(disk, [false, false]);
    assert!(bytes.len() <= 983_040 + 4_160 && held as usize <= bytes.len());
    assert!(!bytes.ends_with("\"continuation\":null}"));
}
