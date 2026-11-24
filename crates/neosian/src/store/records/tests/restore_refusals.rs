//! A restore part's bounds on a FileStore and what a refused one leaves
//! (§40.54 items 8.2 and 12, T1b2b1): memory following the part, the
//! target required empty, one restore open per
//! conversation with an older one discarded only by an opening that passes
//! its checks, every refusal by its exact reason and details, nothing
//! changed by any of them, a restore whose state cannot be read still
//! discarded, and hostile ids and symlinked paths kept inside the root.

use std::fs;
use std::os::unix::fs::symlink;

use serde_json::{Value, json};

use super::memory::peak;
use super::restore_source::{ID, Sent, parts, request, send, source, staged, stream};
use super::{LIMITS, enabled, messages};
use crate::conversation::turn_records::{Header, Restore, Staged, restore_id};
use crate::conversation::{Error, Projection};
use crate::limits;
use crate::store::FileStore;
use crate::store::time::Stamp;
use crate::testing::Scratch;

fn details(error: Error) -> (String, Value) {
    match error {
        Error::Conversation(refused) if refused.code == "agent_conversation_conflict" => (
            refused.details["reason"]
                .as_str()
                .unwrap_or_default()
                .to_string(),
            refused.details.clone(),
        ),
        other => (other.text(), Value::Null),
    }
}

#[test]
fn the_target_must_be_empty() {
    let items = stream(&source(1_000));
    let (_home, store) = enabled("records-restore-occupied");
    store
        .append_turn(
            ID,
            messages(&[json!({"role": "user", "content": "here"})]),
            None,
        )
        .unwrap();
    let refused = send(&store, request(&items[..4])).unwrap_err();
    assert_eq!(details(refused).0, "target_occupied");
    let (_home, store) = enabled("records-restore-projected");
    let entry = Projection {
        turn: 1,
        kind: "log".into(),
        text: "t".into(),
        span: 1,
    };
    store.restore_conversation(ID, &[], &[entry], &[]).unwrap();
    assert_eq!(
        details(send(&store, request(&items[..4])).unwrap_err()).0,
        "target_occupied"
    );
    assert!(
        !store.staging(ID, &restore_id(ID)).unwrap().dir.exists(),
        "nothing staged"
    );
}

/// Move a restore's `touched_at` back by `seconds`.
fn age(store: &FileStore, restore: &str, seconds: i64) {
    let path = store.staging(ID, restore).unwrap().state;
    let mut state: Value = serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
    let touched = Stamp::parse(state["touched_at"].as_str().unwrap()).unwrap();
    state["touched_at"] = json!(Stamp::from_micros(touched.instant() - seconds * 1_000_000).z());
    fs::write(&path, state.to_string()).unwrap();
}

#[test]
fn one_restore_is_open_and_an_older_one_goes_only_with_an_opening_that_passes() {
    let (_home, store) = enabled("records-restore-open");
    let items = stream(&source(1_000));
    let first = send(&store, request(&items[..4])).unwrap();
    let other = |items: &[Sent]| Restore {
        restore: "other".into(),
        ..request(items)
    };
    let (why, extra) = details(send(&store, other(&items[..4])).unwrap_err());
    assert_eq!(
        (why.as_str(), &extra["restore"]),
        ("restore_open", &json!(restore_id(ID)))
    );
    assert!(!store.staging(ID, "other").unwrap().dir.exists());
    age(&store, &restore_id(ID), 86_401);
    let gapped = other(&items[1..4]);
    assert_eq!(details(send(&store, gapped).unwrap_err()).0, "restore_gap");
    assert!(
        store.staging(ID, &restore_id(ID)).unwrap().dir.exists(),
        "kept by a refused opening"
    );
    let opened = send(&store, other(&items[..4])).unwrap();
    assert_eq!(
        (opened.state, opened.freed_bytes),
        (Staged::Open, first.stored_bytes)
    );
    assert!(!store.staging(ID, &restore_id(ID)).unwrap().dir.exists());
}

#[test]
fn every_refusal_has_its_reason_and_changes_nothing() {
    let (_home, store) = enabled("records-restore-refusals");
    let items = stream(&source(200_000));
    send(&store, request(&items[..6])).unwrap();
    let before = send(&store, request(&[])).unwrap().position.unwrap();
    let files = staged(&store);
    let position = json!(before.to_json());
    let record = |at: usize| match &items[at] {
        Sent::Record(turn, record) => (*turn, record.clone()),
        _ => panic!("item {at} is not a record"),
    };
    let header = |at: usize| match &items[at] {
        Sent::Header(header) => header.clone(),
        _ => panic!("item {at} is not a header"),
    };
    let (turn, mut changed) = record(1);
    changed.text = "run the tess".into();
    let mut actor = header(0);
    actor.actor = Some("someone:else".into());
    let mut far = record(7);
    far.1.seq += 1;
    let commit = Restore {
        commit: true,
        ..request(&items[6..7])
    };
    let cases: Vec<(Restore, &str)> = vec![
        (request(&[Sent::Record(far.0, far.1)]), "restore_gap"),
        (request(&[Sent::Record(turn, changed)]), "restore_mismatch"),
        (request(&[Sent::Header(actor)]), "restore_mismatch"),
        (
            request(&[Sent::Header(Header {
                turn: 9,
                ..header(0)
            })]),
            "restore_gap",
        ),
        (commit, "restore_incomplete"),
        (
            Restore {
                restore: "Up".into(),
                ..request(&[])
            },
            "restore id \"Up\" must be 1 to 64 of [a-z0-9_-]",
        ),
    ];
    for (restore, why) in cases {
        let (found, extra) = details(send(&store, restore).unwrap_err());
        assert_eq!(found, why);
        if why.starts_with("restore_") {
            assert_eq!(extra["position"], position, "{why}");
        }
        assert_eq!(staged(&store), files, "{why} changed nothing");
        assert_eq!(send(&store, request(&[])).unwrap().position, Some(before));
    }
    let lowered = limits::Records {
        max_record_turn_bytes: 65_536,
        ..LIMITS
    };
    let big = header(5);
    let (_home, small) = enabled("records-restore-bound");
    send(&small, request(&items[..4])).unwrap();
    let refused = small
        .restore_records(ID, request(&[Sent::Header(big)]), &lowered)
        .unwrap_err();
    assert_eq!(
        refused.text(),
        "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded: 200012 > 65536; this store does not keep a turn this large"
    );
    let home = Scratch::new("records-restore-disabled");
    let plain = FileStore::open(home.path()).unwrap();
    let refused = send(&plain, request(&items[..4])).unwrap_err().text();
    assert!(refused.contains("records are not enabled"), "{refused}");
}

#[test]
fn a_restore_whose_state_cannot_be_read_is_still_discarded() {
    let (_home, store) = enabled("records-restore-unreadable");
    let items = stream(&source(200_000));
    let mut sent = 0;
    for part in [request(&items[..4]), request(&items[4..7])] {
        sent += send(&store, part).unwrap().stored_bytes;
    }
    let staging = store.staging(ID, &restore_id(ID)).unwrap();
    fs::write(&staging.state, "{").unwrap();
    assert!(
        send(&store, request(&[]))
            .unwrap_err()
            .text()
            .contains("a restore's state is unreadable")
    );
    let gone = send(
        &store,
        Restore {
            discard: true,
            ..request(&[])
        },
    )
    .unwrap();
    assert_eq!((gone.state, gone.freed_bytes), (Staged::Discarded, sent));
    assert!(!staging.dir.exists());
}

#[test]
fn an_opening_stopped_before_its_first_state_is_still_discarded() {
    let (_home, store) = enabled("records-restore-stateless");
    let items = stream(&source(1_000));
    let sent = send(&store, request(&items[..4])).unwrap().stored_bytes;
    let staging = store.staging(ID, &restore_id(ID)).unwrap();
    // What a first part stopped after its lines and before its state leaves.
    fs::remove_file(&staging.state).unwrap();
    let files = staged(&store);
    let mut discard = request(&[]);
    discard.discard = true;
    let mut other = request(&items[..4]);
    other.restore = "other".into();
    let probe = send(&store, request(&[])).unwrap();
    assert_eq!((probe.state, probe.position), (Staged::None, None));
    assert_eq!(staged(&store), files, "a probe changes nothing");
    let gone = send(&store, discard.clone()).unwrap();
    assert_eq!((gone.state, gone.position), (Staged::Discarded, None));
    assert_eq!((gone.stored_bytes, gone.freed_bytes), (0, sent));
    assert!(!staging.dir.exists(), "its staged files are removed");
    let again = send(&store, discard).unwrap();
    assert_eq!((again.state, again.freed_bytes), (Staged::None, 0));
    assert_eq!(send(&store, other).unwrap().state, Staged::Open);
}

#[test]
fn hostile_ids_and_symlinked_paths_stay_inside_the_store() {
    let items = stream(&source(1_000));
    let (home, store) = enabled("records-restore-hostile");
    for (conversation, restore) in [
        ("../x", restore_id(ID)),
        (ID, "../x".into()),
        (ID, "a/b".into()),
    ] {
        let part = Restore {
            restore,
            ..request(&items[..4])
        };
        assert!(store.restore_records(conversation, part, &LIMITS).is_err());
    }
    let outside = Scratch::new("records-restore-outside");
    fs::create_dir_all(home.path().join(".records")).unwrap();
    symlink(outside.path(), home.path().join(".records/restore")).unwrap();
    let refused = send(&store, request(&items[..4])).unwrap_err().text();
    assert!(refused.contains("escapes the store root"), "{refused}");
    fs::remove_file(home.path().join(".records/restore")).unwrap();
    send(&store, request(&items[..4])).unwrap();
    let staging = store.staging(ID, &restore_id(ID)).unwrap();
    let elsewhere = Scratch::new("records-restore-elsewhere");
    fs::rename(&staging.records, elsewhere.path().join("records")).unwrap();
    symlink(elsewhere.path().join("records"), &staging.records).unwrap();
    let refused = send(&store, request(&items[4..6])).unwrap_err().text();
    assert!(refused.contains("escapes the store root"), "{refused}");
    assert_eq!(fs::read_dir(outside.path()).unwrap().count(), 0);
    assert_eq!(
        fs::read_dir(elsewhere.path().join("records"))
            .unwrap()
            .count(),
        1
    );
}

/// Each part's peak while restoring a conversation whose long tool result
/// is `size` bytes, in parts of at most 983,040 bytes.
fn part_peaks(name: &str, size: usize) -> Vec<usize> {
    let (_home, store) = enabled(name);
    let items = stream(&source(size));
    parts(&items, 983_040)
        .into_iter()
        .map(|request| {
            let (answer, held) = peak(|| send(&store, request));
            answer.unwrap();
            held
        })
        .collect()
}

#[test]
fn memory_during_a_restore_follows_the_part() {
    let small = part_peaks("records-restore-memory-small", 1 << 20);
    let large = part_peaks("records-restore-memory-large", 16 << 20);
    let (typical, worst) = (*small.iter().max().unwrap(), *large.iter().max().unwrap());
    eprintln!(
        "parts of a 1 MiB turn peaked at {typical}, of a 16 MiB turn ({} parts) at {worst}",
        large.len()
    );
    assert!(large.len() >= 16, "{} parts", large.len());
    assert!(worst <= typical + (256 << 10), "{typical} then {worst}");
    assert!(worst < 4 << 20, "{worst}");
}
