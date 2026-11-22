//! Refusals of a write in parts on a FileStore: every draft conflict and
//! the store's size refusal by name, malformed requests before any draft is
//! read, and a store without the marker, which never writes format 2.

use std::fs;

use serde_json::json;

use super::writing::store_path;
use super::{ACTOR, LIMITS, enabled, example, messages, reason, written};
use crate::conversation::Error;
use crate::conversation::turn_records::{State, Write, records_of};
use crate::store::FileStore;
use crate::testing::Scratch;

#[test]
fn every_draft_conflict_and_the_size_refusal_are_named() {
    let (_home, store) = enabled("records-conflicts");
    let records = records_of(&example()).unwrap();
    let write = |draft: &str, records: Vec<_>, extra: fn(&mut Write)| {
        let mut write = Write {
            draft: draft.into(),
            records,
            actor: Some(ACTOR.into()),
            ..Write::default()
        };
        extra(&mut write);
        store.write_records("c-1", write, &LIMITS).map_err(reason)
    };
    write("d", records[..3].to_vec(), |_| {}).unwrap();
    assert_eq!(
        write("d", records[4..6].to_vec(), |_| {}).unwrap_err(),
        "draft_gap"
    );
    let mut changed = records[1].clone();
    changed.text = "{\"command\":\"make tess\"}".into();
    assert_eq!(
        write("d", vec![changed], |_| {}).unwrap_err(),
        "draft_mismatch"
    );
    assert_eq!(
        write("d", Vec::new(), |w| w.actor = Some("client:other/x".into())).unwrap_err(),
        "draft_actor"
    );
    assert_eq!(
        write("d", Vec::new(), |w| w.turn_bytes = Some(5)).unwrap_err(),
        "draft_size",
        "stated on a later request"
    );
    assert_eq!(
        write("s", records[..3].to_vec(), |w| w.turn_bytes = Some(100)).unwrap_err(),
        "draft_size",
        "past the stated total"
    );
    write("t", records[..1].to_vec(), |w| w.turn_bytes = Some(812_400)).unwrap();
    let short = write("u", vec![records[0].clone()], |w| {
        w.turn_bytes = Some(14);
        w.seal = true;
    });
    assert_eq!(
        short.unwrap_err(),
        "draft_size",
        "a seal whose bytes differ from the stated total"
    );
    let sealed = written(&store, "c-1", "z", &example());
    assert_eq!(sealed.state, State::Sealed);
    assert_eq!(
        write("z", records[15..16].to_vec(), |_| {}).unwrap().state,
        State::Sealed,
        "a repeat after a seal"
    );
    let more = records_of(&messages(&[json!({"role": "user", "content": "late"})])).unwrap();
    let mut late = more[0].clone();
    late.seq = 16;
    assert_eq!(write("z", vec![late], |_| {}).unwrap_err(), "draft_sealed");
    assert_eq!(
        write("z", Vec::new(), |w| w.discard = true).unwrap_err(),
        "draft_sealed"
    );
    let probe = write("z", Vec::new(), |_| {}).unwrap();
    assert_eq!(
        (probe.state, probe.turn.map(|t| t.turn)),
        (State::Sealed, sealed.turn.map(|t| t.turn))
    );
    for open in 0..14 {
        write(&format!("o{open}"), records[..1].to_vec(), |_| {}).unwrap();
    }
    let seventeenth = write("o14", records[..1].to_vec(), |_| {}).unwrap_err();
    assert_eq!(
        seventeenth, "drafts_open",
        "d, t and fourteen more are open"
    );
    let limits = crate::limits::Records {
        max_record_turn_bytes: 65_536,
        ..LIMITS
    };
    let over = |write: Write| {
        store
            .write_records("c-2", write, &limits)
            .map_err(reason)
            .unwrap_err()
    };
    let stated = over(Write {
        draft: "x".into(),
        records: records[..1].to_vec(),
        turn_bytes: Some(70_000),
        ..Write::default()
    });
    assert_eq!(
        stated,
        "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded: 70000 > 65536; this store does not keep a turn this large"
    );
    let grown = over(Write {
        draft: "y".into(),
        records: records[..3].to_vec(),
        ..Write::default()
    });
    assert!(
        grown.starts_with("resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded: 812381 > 65536"),
        "{grown}"
    );
    assert!(
        !store_path(&store, "c-2", "y.jsonl").exists(),
        "a refused part stores nothing"
    );
}

#[test]
fn malformed_requests_are_refused_before_any_draft_is_read() {
    let (_home, store) = enabled("records-malformed");
    let records = records_of(&example()).unwrap();
    let refused = |write: Write| reason(store.write_records("c-1", write, &LIMITS).unwrap_err());
    assert!(
        refused(Write {
            draft: "bad id".into(),
            ..Write::default()
        })
        .contains("[A-Za-z0-9_-]")
    );
    let discard = Write {
        draft: "d".into(),
        discard: true,
        seal: true,
        ..Write::default()
    };
    assert!(refused(discard).contains("no records and no seal"));
    let gapped = Write {
        draft: "d".into(),
        records: vec![records[0].clone(), records[2].clone()],
        ..Write::default()
    };
    assert!(refused(gapped).contains("consecutive seq"));
    assert!(
        refused(Write {
            draft: "d".into(),
            seal: true,
            ..Write::default()
        })
        .contains("at least one record")
    );
    let mut wrong = records[0].clone();
    wrong.kind = crate::conversation::turn_records::Kind::Reply;
    assert!(
        refused(Write {
            draft: "d".into(),
            records: vec![wrong],
            ..Write::default()
        })
        .contains("cannot belong")
    );
}

#[test]
fn a_store_without_the_marker_never_writes_format_2() {
    let home = Scratch::new("records-disabled");
    let store = FileStore::open(home.path()).unwrap();
    assert!(!store.records_enabled().unwrap());
    let records = records_of(&example()).unwrap();
    let write = Write {
        draft: "d".into(),
        records,
        seal: true,
        ..Write::default()
    };
    let refused = store.write_records("c-1", write, &LIMITS).unwrap_err();
    assert!(
        matches!(&refused, Error::Memory(error) if error.code == "agent_configuration_error"),
        "{refused:?}"
    );
    assert!(
        !store.conversation_dir("c-1").unwrap().exists(),
        "nothing written"
    );
    let (_enabled_home, other) = enabled("records-planted");
    written(&other, "c-1", "d", &example());
    let log = other.conversation_dir("c-1").unwrap().join("turns.jsonl");
    let header = fs::read_to_string(&log).unwrap();
    home.plant("conversations/c-1/turns.jsonl", &header);
    let error = store.read_turns("c-1", 0, None).unwrap_err().text();
    assert!(
        error.contains("turn-log line 1 format 2 is newer than supported"),
        "{error}"
    );
    assert!(
        store
            .last_turn_number("c-1")
            .unwrap_err()
            .text()
            .contains("newer than supported")
    );
    assert!(
        other.enable_records().is_ok_and(|changed| !changed),
        "enabling again changes nothing"
    );
}
