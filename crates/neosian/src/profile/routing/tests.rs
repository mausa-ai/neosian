//! Record methods through a profile (the T1e2c slice): they reach its
//! history source only after the selection admits them, never its memory
//! source; a search is limited to the admitted conversations and sends a
//! server the query as written; a restore and the sweep are refused. Each
//! profile is built over stores opened here, as `routing/open.rs` opens
//! them from bindings, so no credential is read from the environment.

use std::collections::BTreeMap;
use std::sync::Arc;

use serde_json::{Value, json};

use super::Routed;
use super::records::HistoryRecords;
use crate::conversation::message;
use crate::conversation::turn_records::{RecordHit, RecordRead, Restore, Write, records_of};
use crate::profile::Profile;
use crate::profile::model::{CaptureMode, History};
use crate::record::landing::tests::http::{Server, refusal};
use crate::remote::RemoteStore;
use crate::remote::records::{Failure, RecordQuery};
use crate::state::records::{Fault, Records, Source};
use crate::store::FileStore;
use crate::testing::Scratch;

const OUTSIDE: &str = "[agent_configuration_error] operation is outside the selected profile";

fn profile(prefix: Option<&str>, listed: &[&str], capture: CaptureMode) -> Profile {
    let mut profile = Profile::new("work".into());
    profile.sources.push("history".into());
    profile.history = Some(History {
        source: "history".into(),
        scope: "workspace:history".into(),
        prefix: prefix.map(str::to_string),
        conversations: listed.iter().map(|id| id.to_string()).collect(),
    });
    profile.capture.mode = capture;
    profile
}

fn routed(profile: Profile, memory: Source, history: Source) -> HistoryRecords {
    let sources = BTreeMap::from([("workspace".into(), memory), ("history".into(), history)]);
    HistoryRecords::new(Arc::new(Routed { profile, sources }))
}

fn enabled(scratch: &Scratch) -> Source {
    let store = FileStore::open(scratch.path()).unwrap();
    store.enable_records().unwrap();
    Source::File(store)
}

fn remote(server: &Server) -> Source {
    Source::Remote(Box::new(
        RemoteStore::connect(&server.url, "token").unwrap(),
    ))
}

/// A sealed one-record turn saying `text`.
fn sealed(text: &str) -> Write {
    let said = message::from_json(&json!({"role": "user", "content": text})).unwrap();
    Write {
        draft: format!("d-{}", text.len()),
        records: records_of(&[said]).unwrap(),
        seal: true,
        actor: Some("client:default/claude-code:t".into()),
        ..Write::default()
    }
}

fn denied(fault: Fault) -> String {
    match fault {
        Fault::Local(error) => error.text(),
        Fault::Remote(failure) => panic!("asked the server: {failure}"),
    }
}

/// The routes a server was asked, the handshake left out.
fn asked(server: &Server) -> Vec<(String, Value)> {
    let log = server.log.lock().unwrap();
    log.iter()
        .filter(|(path, _)| path != "/v1/capabilities")
        .cloned()
        .collect()
}

fn ids(hits: Vec<RecordHit>) -> Vec<String> {
    hits.into_iter().map(|hit| hit.conversation_id).collect()
}

fn query(text: &str, conversations: Option<&[&str]>) -> RecordQuery {
    RecordQuery {
        query: text.into(),
        conversations: conversations.map(|ids| ids.iter().map(|id| id.to_string()).collect()),
        ..RecordQuery::default()
    }
}

#[test]
fn a_record_write_reaches_the_history_source_only_after_admission() {
    let (memory, history) = (
        Scratch::new("routing-memory"),
        Scratch::new("routing-history"),
    );
    let profiled = routed(
        profile(Some("w-"), &[], CaptureMode::Local),
        enabled(&memory),
        enabled(&history),
    );
    let records = profiled.records().unwrap().unwrap();
    let written = records.write("w-a", sealed("kept")).unwrap();
    assert_eq!(written.state.as_str(), "sealed");
    let refused = records.write("x-b", sealed("outside")).unwrap_err();
    assert_eq!(denied(refused), OUTSIDE);
    let paused = routed(
        profile(Some("w-"), &[], CaptureMode::Off),
        enabled(&memory),
        enabled(&history),
    );
    let refused = paused
        .records()
        .unwrap()
        .unwrap()
        .write("w-c", sealed("off"));
    assert_eq!(denied(refused.unwrap_err()), OUTSIDE);
    let stored = |scratch: &Scratch| FileStore::open(scratch.path()).unwrap().conversations();
    assert_eq!(stored(&history).unwrap(), ["w-a"]);
    assert!(
        stored(&memory).unwrap().is_empty(),
        "never the memory source"
    );
}

#[test]
fn a_record_search_finds_only_what_the_selection_admits() {
    let history = Scratch::new("routing-search");
    let store = FileStore::open(history.path()).unwrap();
    store.enable_records().unwrap();
    let direct = Source::File(FileStore::open(history.path()).unwrap());
    let keeper = direct.keeper().unwrap().unwrap();
    for id in ["w-a", "w-b", "x-c"] {
        keeper.write(id, sealed("a needle here")).unwrap();
    }
    let memory = Scratch::new("routing-search-memory");
    let listed = routed(
        profile(None, &["w-a", "w-b"], CaptureMode::Off),
        enabled(&memory),
        Source::File(store),
    );
    let records = listed.records().unwrap().unwrap();
    // The query is folded by the store; the selection is the profile's.
    let mut implicit = ids(records.search(&query("NEEDLE", None)).unwrap());
    implicit.sort();
    assert_eq!(implicit, ["w-a", "w-b"]);
    assert_eq!(
        ids(records.search(&query("needle", Some(&["w-b"]))).unwrap()),
        ["w-b"]
    );
    assert!(
        records
            .search(&query("needle", Some(&[])))
            .unwrap()
            .is_empty()
    );
    let refused = records
        .search(&query("needle", Some(&["w-a", "x-c"])))
        .unwrap_err();
    assert_eq!(denied(refused), OUTSIDE);
    let nothing = routed(
        profile(Some("zz-"), &[], CaptureMode::Off),
        enabled(&memory),
        Source::File(FileStore::open(history.path()).unwrap()),
    );
    let none = nothing
        .records()
        .unwrap()
        .unwrap()
        .search(&query("needle", None));
    assert!(
        none.unwrap().is_empty(),
        "an empty selection answers no hits"
    );
}

#[test]
fn a_remote_history_source_is_sent_the_admitted_list_and_the_query_as_written() {
    let history = Server::new(|path, _| {
        path.ends_with("search_records")
            .then(|| (200, vec![], json!({"hits": []})))
    });
    let memory = Server::new(|_, _| None);
    let listed = routed(
        profile(None, &["w-a", "w-b"], CaptureMode::Off),
        remote(&memory),
        remote(&history),
    );
    let records = listed.records().unwrap().unwrap();
    assert!(
        records
            .search(&query("Ünïcode  NEEDLE", None))
            .unwrap()
            .is_empty()
    );
    let expected = json!({"query": "Ünïcode  NEEDLE", "conversations": ["w-a", "w-b"]});
    let searched = ("/v1/conversation/search_records".to_string(), expected);
    assert_eq!(asked(&history), std::slice::from_ref(&searched));
    // Nothing named, nothing admitted, nothing readable: nothing is sent.
    assert!(
        records
            .search(&query("needle", Some(&[])))
            .unwrap()
            .is_empty()
    );
    let refused = records
        .search(&query("needle", Some(&["x-c"])))
        .unwrap_err();
    assert_eq!(denied(refused), OUTSIDE);
    let refused = records.read("x-c", &RecordRead::default(), &mut |_| Ok(true));
    assert_eq!(denied(refused.unwrap_err()), OUTSIDE);
    let refused = records.write("w-a", sealed("capture is off")).unwrap_err();
    assert_eq!(denied(refused), OUTSIDE);
    assert_eq!(asked(&history), [searched]);
    // A grant's refusal comes back as the server sent it.
    history.answer(|path, _| {
        path.ends_with("search_records")
            .then(|| refusal(403, "forbidden", "conversation w-b is not granted"))
    });
    match records.search(&query("needle", None)) {
        Err(Fault::Remote(Failure::Refused(refused))) => {
            assert_eq!(
                (refused.status, refused.code.as_deref()),
                (403, Some("forbidden"))
            );
        }
        other => panic!("not the server's refusal: {other:?}"),
    }
    assert!(asked(&memory).is_empty(), "never the memory source");
}

#[test]
fn a_restore_and_the_sweep_never_run_through_a_profile() {
    let history = Server::new(|_, _| None);
    let profiled = routed(
        profile(Some("w-"), &[], CaptureMode::Shared),
        remote(&Server::new(|_, _| None)),
        remote(&history),
    );
    let records = profiled.records().unwrap().unwrap();
    for write in [false, true] {
        assert_eq!(denied(records.sweep(50, None, write).unwrap_err()), OUTSIDE);
    }
    let probe = Restore {
        restore: "r".into(),
        ..Restore::default()
    };
    assert_eq!(denied(records.restore("w-a", probe).unwrap_err()), OUTSIDE);
    assert!(asked(&history).is_empty());
}

#[test]
fn a_record_read_through_a_profile_follows_its_pages_forward() {
    let (memory, history) = (
        Scratch::new("routing-read-memory"),
        Scratch::new("routing-read"),
    );
    let profiled = routed(
        profile(Some("w-"), &[], CaptureMode::Local),
        enabled(&memory),
        enabled(&history),
    );
    let records = profiled.records().unwrap().unwrap();
    for text in ["one", "second", "the third"] {
        records.write("w-a", sealed(text)).unwrap();
    }
    let read = RecordRead {
        page_bytes: 1,
        ..RecordRead::default()
    };
    let (mut pages, mut turns, mut continuations) = (0, Vec::new(), Vec::new());
    records
        .read("w-a", &read, &mut |page| {
            pages += 1;
            turns.extend(page.records.iter().map(|record| record.turn));
            continuations.push(page.continuation);
            Ok(true)
        })
        .unwrap();
    assert_eq!(turns, [1, 2, 3]);
    assert_eq!(
        continuations.pop(),
        Some(None),
        "the last page ends the read"
    );
    let mut distinct = continuations.clone();
    distinct.dedup();
    assert!(pages > 1 && distinct == continuations);
}
