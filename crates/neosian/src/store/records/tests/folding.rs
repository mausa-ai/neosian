//! `records fold` on a FileStore (§40.54 item 21, T1b2c4): a migrated
//! conversation folded back to its first bytes, a turn written as records
//! folded into the whole turn its messages make, as a store that never
//! kept records holds it, every read and search answering as before, a
//! repeat changing nothing, pending work refusing
//! its conversation or going with `--discard-pending` only once the whole
//! conversation has been staged, and a turn past the legacy bound refused
//! by name before it is assembled. Seals repeated after a fold are in
//! `receipts`, interruptions in `recovery`, and the two places a name is
//! stored in, each under its own spelling, in `places`.

mod places;
mod receipts;
mod recovery;

use std::fs;

use serde_json::json;

use super::memory::peak;
use super::migrating::{legacy, log, migrate, say, seen, settled, whole};
use super::searching::twins;
use super::{LIMITS, enabled, example, messages, part, written};
use crate::conversation::Error;
use crate::conversation::rows_header::parse_row;
use crate::conversation::turn_records::migration::Folded;
use crate::conversation::turn_records::records_of;
use crate::limits;
use crate::store::FileStore;

/// A fold from a listing read just before it.
fn fold_with(
    store: &FileStore,
    id: &str,
    discard: bool,
    limits: &limits::Records,
) -> Result<Folded, Error> {
    store.fold_records(id, &mut store.record_places().unwrap(), discard, limits)
}

fn fold(store: &FileStore, id: &str) -> Folded {
    fold_with(store, id, false, &LIMITS).unwrap()
}

fn folded(id: &str, folded: u64, whole: u64, skeletons: u64) -> Folded {
    Folded {
        conversation_id: id.into(),
        folded,
        whole,
        skeletons,
        drafts: 0,
        restores: 0,
    }
}

#[test]
fn a_migrated_conversation_folds_back_to_its_first_bytes_and_reads_as_before() {
    let (home, store) = legacy("records-fold-mixed");
    // A row a migration keeps: the same turn in another spelling.
    let spaced = "{\"neosian_format\": 1, \"turn\": 7, \"created_at\": \"2026-10-02T09:15:00Z\", \
                  \"messages\": [{\"role\": \"user\", \"content\": \"zeta spaced\"}], \"actor\": null}\n";
    let mut text = fs::read_to_string(log(&store, "c-1")).unwrap();
    text.push_str(spaced);
    fs::write(log(&store, "c-1"), &text).unwrap();
    let first = [text, fs::read_to_string(log(&store, "c-2")).unwrap()];
    let before = seen(&store);
    assert_eq!(migrate(&store, "c-1").kept.spelling, 1);
    assert_eq!(migrate(&store, "c-2").migrated, 1);
    assert_eq!(seen(&store), before);

    assert_eq!(fold(&store, "c-1"), folded("c-1", 5, 1, 1));
    assert_eq!(fold(&store, "c-2"), folded("c-2", 1, 0, 0));
    assert_eq!(seen(&store), before);
    assert!(settled(&store, "c-1") && settled(&store, "c-2"));
    assert_eq!(fs::read_to_string(log(&store, "c-2")).unwrap(), first[1]);
    let after = fs::read_to_string(log(&store, "c-1")).unwrap();
    let rows: Vec<_> = first[0].lines().zip(after.lines()).collect();
    assert_eq!(rows.len(), 7);
    for (number, (was, is)) in rows.into_iter().enumerate() {
        // Every row reads on a store without records; only the turn that
        // was written as records has new bytes, a whole row now.
        parse_row(is, number + 1, "c-1", false).unwrap();
        assert_eq!(was == is, number != 4, "row {}", number + 1);
    }
    let directory = home.path().join("conversations/c-1");
    assert!(
        !directory.join("records").exists(),
        "no records file is left"
    );
    assert!(directory.join(".drafts/d1.sealed").is_file());
    assert!(directory.join(".drafts/folded/5").is_file());
    assert!(!home.path().join("conversations/c-2/.drafts").exists());

    assert_eq!(fold(&store, "c-1"), folded("c-1", 0, 6, 1));
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
    assert_eq!(next.turn.unwrap().turn, 9);
    assert_eq!(whole(&store, "c-1").len(), 9);
}

#[test]
fn a_store_that_began_with_records_reads_and_searches_the_same_once_folded() {
    // The twin holds the same turns whole and never kept records.
    let (_a, store, _b, twin) = twins("records-fold-native");
    let before = seen(&store);
    assert_eq!(fold(&store, "c-1"), folded("c-1", 2, 2, 0));
    assert_eq!(fold(&store, "c-2"), folded("c-2", 1, 0, 0));
    assert_eq!(seen(&store), before);
    for id in ["c-1", "c-2"] {
        let folded = fs::read_to_string(log(&store, id)).unwrap();
        assert_eq!(folded, fs::read_to_string(log(&twin, id)).unwrap(), "{id}");
    }
}

#[test]
fn pending_work_refuses_its_conversation_or_goes_only_after_the_whole_of_it_is_staged() {
    let (home, store) = enabled("records-fold-pending");
    say(
        &store,
        "c-1",
        &[json!({"role": "user", "content": "one"})],
        None,
    );
    written(&store, "c-1", "d1", &example());
    let open = records_of(&messages(&[json!({"role": "user", "content": "open"})])).unwrap();
    part(&store, "c-1", "d2", open.clone(), false).unwrap();
    part(&store, "c-9", "d3", open, false).unwrap();
    let (original, before) = (fs::read(log(&store, "c-1")).unwrap(), whole(&store, "c-1"));
    let state = home.path().join("conversations/c-1/.drafts/d2.state");

    let refused = fold_with(&store, "c-1", false, &LIMITS).unwrap_err().text();
    assert!(
        refused.contains("conversation c-1 holds pending work (1 open drafts, 0 staged restores)"),
        "{refused}"
    );
    // The flag does not reach the draft while a turn refuses the conversation.
    let low = limits::Records {
        max_turn_bytes: 65_536,
        ..LIMITS
    };
    let long = fold_with(&store, "c-1", true, &low).unwrap_err().text();
    assert!(
        long.contains(
            "resource limit NEOSIAN_MAX_TURN_BYTES exceeded: turn 2 of c-1 holds 812400 > 65536 \
             bytes; raise the setting to fold it"
        ),
        "{long}"
    );
    assert!(state.is_file() && settled(&store, "c-1"));
    assert_eq!(fs::read(log(&store, "c-1")).unwrap(), original);
    assert_eq!(whole(&store, "c-1"), before, "the conversation reads whole");

    let done = fold_with(&store, "c-1", true, &LIMITS).unwrap();
    let both = Folded {
        drafts: 1,
        ..folded("c-1", 1, 1, 0)
    };
    assert_eq!(done, both);
    assert!(!state.exists());
    assert_eq!(whole(&store, "c-1"), before);
    // A conversation of drafts alone, which no listing shows.
    assert_eq!(store.conversations().unwrap(), ["c-1"]);
    assert_eq!(store.record_places().unwrap().names(), ["c-1", "c-9"]);
    let alone = fold_with(&store, "c-9", true, &LIMITS).unwrap();
    assert_eq!((alone.folded, alone.drafts), (0, 1));
    assert!(
        !home
            .path()
            .join("conversations/c-9/.drafts/d3.state")
            .exists()
    );
}

#[test]
fn a_fold_needs_records_enabled_and_a_valid_name() {
    let (home, store) = enabled("records-fold-refusals");
    written(
        &store,
        "c-1",
        "d1",
        &messages(&[json!({"role": "user", "content": "one"})]),
    );
    let invalid = fold_with(&store, "../x", false, &LIMITS)
        .unwrap_err()
        .text();
    assert!(
        invalid.contains("agent_conversation_id_invalid"),
        "{invalid}"
    );
    let marker = home.path().join(".records/format");
    let text = fs::read(&marker).unwrap();
    fs::remove_file(&marker).unwrap();
    let plain = fold_with(&store, "c-1", false, &LIMITS).unwrap_err().text();
    assert!(plain.contains("records are not enabled"), "{plain}");
    fs::write(&marker, text).unwrap();
    assert_eq!(fold(&store, "c-1"), folded("c-1", 1, 0, 0));
}

/// The most a fold of `turns` turns of 200,000 bytes held at once.
fn held(turns: u64) -> usize {
    let (_home, store) = enabled(&format!("records-fold-memory-{turns}"));
    let result = format!("{}\n", "r".repeat(99)).repeat(2_000);
    for number in 0..turns {
        let turn = messages(&[
            json!({"role": "user", "content": "run it"}),
            json!({"role": "tool", "content": result, "tool_call_id": "t"}),
        ]);
        written(&store, "c-1", &format!("d{number}"), &turn);
    }
    let (done, held) = peak(|| fold(&store, "c-1"));
    assert_eq!(done, folded("c-1", turns, 0, 0));
    held
}

#[test]
fn memory_during_a_fold_follows_one_turn_not_the_log() {
    let (few, many) = (held(4), held(48));
    eprintln!("fold peaks: {few} bytes for 4 turns of 200,000 bytes, {many} for 48");
    assert!(
        many <= few + (64 << 10),
        "4 turns held {few}, 48 held {many}"
    );
    assert!(many < 2 << 20, "a 200,000-byte turn held {many} bytes");
}
