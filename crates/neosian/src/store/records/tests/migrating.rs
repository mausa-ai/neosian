//! `records migrate` on a FileStore (§40.54 item 21, T1b2c3): a mixed
//! conversation migrated in place with every read and search answering as
//! before, each migrated turn folding back to its stored line, a repeat
//! changing nothing, a header's owner shared with a client's draft, and
//! the refusals. Rows that stay whole are in `kept`, interruptions in
//! `recovery`, the legacy bound and memory in `bounds`.

mod bounds;
mod kept;
mod recovery;

use std::fs;
use std::path::PathBuf;

use serde_json::{Value, json};

use super::super::migrate::{Kept, Migrated, migration_id};
use super::searching::{answer, request};
use super::{ACTOR, LIMITS, enabled, example, messages, part, written};
use crate::conversation::Projection;
use crate::conversation::rank::Order;
use crate::conversation::rows::render_turn;
use crate::conversation::rows_header::{Row, parse_row};
use crate::conversation::search::parse_query;
use crate::conversation::turn_records::{RecordRead, RecordSearch, State};
use crate::pyfmt::dumps;
use crate::store::FileStore;
use crate::testing::Scratch;
use crate::wire::encode;

const QUERIES: [&str; 5] = ["alpha", "tests pass", "zeta", "make test", "bash tests"];

pub(super) fn log(store: &FileStore, id: &str) -> PathBuf {
    store.conversation_dir(id).unwrap().join("turns.jsonl")
}

pub(super) fn say(store: &FileStore, id: &str, values: &[Value], actor: Option<&str>) {
    let actor = actor.map(str::to_string);
    store.append_turn(id, messages(values), actor).unwrap();
}

pub(super) fn migrate(store: &FileStore, id: &str) -> Migrated {
    store.migrate_records(id, &LIMITS).unwrap()
}

fn moved(id: &str, migrated: u64, records: u64, skeletons: u64) -> Migrated {
    Migrated {
        conversation_id: id.into(),
        migrated,
        records,
        skeletons,
        kept: Kept::default(),
    }
}

/// No migration directory is left in a conversation.
pub(super) fn settled(store: &FileStore, id: &str) -> bool {
    let entries = fs::read_dir(store.conversation_dir(id).unwrap()).unwrap();
    entries
        .map(|entry| entry.unwrap().file_name().into_string().unwrap())
        .all(|name| !name.starts_with(".migrate") && !name.starts_with(".fold"))
}

/// A conversation's turns as `read_turns` answers them.
pub(super) fn whole(store: &FileStore, id: &str) -> Vec<String> {
    let turns = store.read_turns(id, 0, None).unwrap();
    turns
        .iter()
        .map(|turn| dumps(&encode::turn(turn), true))
        .collect()
}

/// What every reader and search answers over the store.
pub(super) fn seen(store: &FileStore) -> Vec<String> {
    let mut all = Vec::new();
    for id in store.conversations().unwrap() {
        all.extend(whole(store, &id));
        let mut read = RecordRead::default();
        loop {
            let mut visit = |item: crate::conversation::turn_records::ReadItem| {
                all.push(item.to_json().to_string());
                Ok(())
            };
            let Some(next) = store.read_records(&id, &read, &mut visit).unwrap() else {
                break;
            };
            read.continuation = Some(next.encode(&read.fingerprint(&id)));
        }
        let (last, first) = (store.last_turn_number(&id), store.first_turn_stamp(&id));
        all.push(format!("{:?} {:?}", last.unwrap(), first.unwrap()));
        all.push(format!(
            "{:?}",
            store.read_projections(&id, 0, None).unwrap()
        ));
    }
    all.push(format!("{:?}", store.turn_redactions(None, None, 50)));
    for query in QUERIES {
        for order in [Order::Newest, Order::Relevance] {
            let ranked = answer(store, &request(query, order), Some(1 << 30));
            assert!(!ranked.is_empty(), "{query} matches");
            all.push(format!("{ranked:?}"));
            let search = RecordSearch::new(query, None, order, None, None, 50).unwrap();
            all.push(format!("{:?}", store.search_records(&search).unwrap()));
        }
        let terms = parse_query(query).unwrap();
        for turn in store.search_turns(&terms, None, 50).unwrap() {
            all.push(dumps(&encode::turn(&turn), true));
        }
    }
    all
}

/// c-1: a small turn, item 3.8's turn, one with every opaque field, a
/// skeleton, a turn written as records, a last whole turn; c-2: one turn.
pub(super) fn legacy(name: &str) -> (Scratch, FileStore) {
    let (home, store) = enabled(name);
    let user = |text: &str| json!({"role": "user", "content": text});
    say(&store, "c-1", &[user("alpha tests begin")], Some("cli:t"));
    store
        .append_turn("c-1", example(), Some(ACTOR.into()))
        .unwrap();
    let opaque = json!({"role": "assistant",
        "content": [{"type": "text", "text": "zeta h\u{e9}llo \u{754c} \u{1} bash"},
                    {"type": "image", "url": "https://x/y.png"}],
        "tool_calls": [{"id": "t9", "name": "Bash", "arguments": {"cmd": "ls"}, "extra": {"k": [1, 2]}}],
        "reasoning": {"steps": ["a", "b"]}, "extra": {"vendor": {"x": 1.5}}});
    say(&store, "c-1", &[user("zeta next"), opaque], None);
    say(&store, "c-1", &[user("alpha erase me")], Some("cli:t"));
    assert_eq!(
        store
            .redact_turns("c-1", None, Some(&[4]), Some("cli:t"))
            .unwrap(),
        1
    );
    written(
        &store,
        "c-1",
        "d1",
        &messages(&[user("zeta alpha in parts")]),
    );
    let done = json!({"role": "assistant", "content": "alpha done, tests pass"});
    say(&store, "c-1", &[done], None);
    say(
        &store,
        "c-2",
        &[user("alpha again, make test")],
        Some("cli:t"),
    );
    let entry = Projection {
        turn: 3,
        kind: "epoch".into(),
        text: "set up".into(),
        span: 3,
    };
    store.append_projections("c-1", &[entry]).unwrap();
    (home, store)
}

#[test]
fn a_mixed_conversation_migrates_in_place_and_reads_and_searches_as_before() {
    let (_home, store) = legacy("records-migrate-mixed");
    let (before, original) = (
        seen(&store),
        fs::read_to_string(log(&store, "c-1")).unwrap(),
    );
    let sealed = store
        .conversation_dir("c-1")
        .unwrap()
        .join(".drafts/d1.sealed");
    let receipt = fs::read(&sealed).unwrap();
    assert_eq!(migrate(&store, "c-1"), moved("c-1", 4, 1, 1));
    assert_eq!(migrate(&store, "c-2"), moved("c-2", 1, 0, 0));
    assert_eq!(seen(&store), before);
    assert!(settled(&store, "c-1") && settled(&store, "c-2"));
    assert_eq!(fs::read(&sealed).unwrap(), receipt, "the seal's receipt");

    let after = fs::read_to_string(log(&store, "c-1")).unwrap();
    let (old, new): (Vec<_>, Vec<_>) = (original.lines().collect(), after.lines().collect());
    assert_eq!(old.len(), new.len());
    let mut folded = String::new();
    for (number, line) in new.iter().enumerate() {
        match parse_row(line, number + 1, "c-1", true).unwrap() {
            Row::Records(header) if header.draft == "d1" => assert_eq!(number, 4),
            Row::Records(header) => {
                assert_eq!(header.draft, migration_id("c-1"));
                assert_eq!(header.turn, number as u64 + 1);
                let row = Row::Records(header);
                folded.push_str(&render_turn(&store.whole("c-1", row, 1 << 30).unwrap()));
                continue;
            }
            Row::Whole(turn) => assert!(turn.redacted && number == 3, "row {}", number + 1),
        }
        assert_eq!(*line, old[number], "row {} keeps its bytes", number + 1);
        folded.push_str(&format!("{line}\n"));
    }
    assert_eq!(folded, original, "a fold gives the first log back");

    assert_eq!(migrate(&store, "c-1"), moved("c-1", 0, 5, 1));
    assert_eq!(fs::read_to_string(log(&store, "c-1")).unwrap(), after);
    say(
        &store,
        "c-1",
        &[json!({"role": "user", "content": "more"})],
        None,
    );
    let next = written(
        &store,
        "c-1",
        "d2",
        &messages(&[json!({"role": "user", "content": "parts"})]),
    );
    assert_eq!((next.state, next.turn.unwrap().turn), (State::Sealed, 8));
    assert_eq!(store.read_turns("c-1", 6, None).unwrap().len(), 2);
}

#[test]
fn a_header_sharing_its_owner_with_a_clients_draft_leaves_that_drafts_receipt() {
    let (_home, store) = enabled("records-migrate-owner");
    let user = |text: &str| json!({"role": "user", "content": text});
    say(&store, "c-1", &[user("one")], None);
    let owner = migration_id("c-1");
    written(&store, "c-1", &owner, &messages(&[user("two")]));
    say(&store, "c-1", &[user("three")], None);
    assert_eq!(migrate(&store, "c-1"), moved("c-1", 2, 1, 0));
    let directory = store.conversation_dir("c-1").unwrap();
    let sealed = directory.join(format!(".drafts/{owner}.sealed"));
    assert_eq!(
        store.redact_turns("c-1", None, Some(&[1]), None).unwrap(),
        1
    );
    assert!(sealed.is_file() && !directory.join("records/1.jsonl").exists());
    let again = part(&store, "c-1", &owner, Vec::new(), false).unwrap();
    assert_eq!((again.state, again.turn.unwrap().turn), (State::Sealed, 2));
    assert_eq!(
        store.redact_turns("c-1", None, Some(&[2]), None).unwrap(),
        1
    );
    assert!(!sealed.exists());
    let left = store.read_turns("c-1", 0, None).unwrap();
    assert_eq!(left[2].messages.len(), 1, "the other migrated turn reads");
}

#[test]
fn a_store_without_records_an_invalid_id_and_an_absent_conversation() {
    let home = Scratch::new("records-migrate-plain");
    let plain = FileStore::open(home.path()).unwrap();
    say(
        &plain,
        "c-1",
        &[json!({"role": "user", "content": "one"})],
        None,
    );
    let before = fs::read(log(&plain, "c-1")).unwrap();
    let refused = plain.migrate_records("c-1", &LIMITS).unwrap_err().text();
    assert!(refused.contains("records are not enabled"), "{refused}");
    assert_eq!(fs::read(log(&plain, "c-1")).unwrap(), before);
    assert!(plain.enable_records().unwrap());
    let invalid = plain.migrate_records("../x", &LIMITS).unwrap_err().text();
    assert!(
        invalid.contains("agent_conversation_id_invalid"),
        "{invalid}"
    );
    assert_eq!(migrate(&plain, "absent"), moved("absent", 0, 0, 0));
    assert!(!home.path().join("conversations/absent").exists());
    // The stored names are exact, and a file or a foreign name is not one.
    let conversations = home.path().join("conversations");
    fs::write(conversations.join("stray"), "a file").unwrap();
    fs::create_dir(conversations.join("a b")).unwrap();
    assert_eq!(plain.stored_conversations().unwrap(), ["c-1"]);
    assert_eq!(fs::read(log(&plain, "c-1")).unwrap(), before);
}
