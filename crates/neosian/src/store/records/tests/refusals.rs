//! What a refused write in parts leaves on a FileStore, after the review of
//! 2481787: draft ids that cannot share files on a case-folding file
//! system, a refused request that changes nothing, the store's bound at
//! the seal, an unreadable draft state that blocks no other draft, the
//! stale cleanup's edge, and the mutations M5, M8 and M9 caught.

use std::fs;
use std::time::{Duration, SystemTime};

use serde_json::{Value, json};

use super::super::stale::stale;
use super::writing::store_path;
use super::{LIMITS, enabled, example, messages, part, parts, reason, spelled, turns};
use crate::conversation::turn_records::{Sizes, State, TurnRecord, Write, records_of};
use crate::limits;
use crate::store::FileStore;
use crate::store::time::Stamp;

fn small(text: &str) -> Vec<TurnRecord> {
    records_of(&messages(&[json!({"role": "user", "content": text})])).unwrap()
}

/// Move a draft's `touched_at` back by `seconds`.
fn age(store: &FileStore, draft: &str, seconds: i64) {
    let path = store_path(store, "c-1", &format!("{draft}.state"));
    let mut state: Value = serde_json::from_str(&fs::read_to_string(&path).unwrap()).unwrap();
    let touched = Stamp::parse(state["touched_at"].as_str().unwrap()).unwrap();
    state["touched_at"] = json!(Stamp::from_micros(touched.instant() - seconds * 1_000_000).z());
    fs::write(&path, state.to_string()).unwrap();
}

/// Finding 4: on a case-folding file system `Abc` and `abc` shared files,
/// and a probe of a draft never written answered `sealed`.
#[test]
fn a_draft_id_is_lowercase_letters_digits_dash_and_underscore() {
    let (_home, store) = enabled("records-refusals-ids");
    let long = "a".repeat(65);
    for id in ["Abc", "ABC", "a.b", "a b", "", "é", "a/b", long.as_str()] {
        let refused = part(&store, "c-1", id, small("x"), false)
            .unwrap_err()
            .text();
        assert!(
            refused.contains("must be 1 to 64 of [a-z0-9_-]"),
            "{id:?}: {refused}"
        );
    }
    let longest = "a".repeat(64);
    for id in [
        "abc",
        "a-b_c",
        "0",
        "i0123456789abcdef0123456789abcdef",
        &longest,
    ] {
        assert_eq!(
            part(&store, "c-1", id, small("x"), false).unwrap().state,
            State::Open
        );
    }
}

/// Finding 5: stale drafts were discarded before an opening's own checks,
/// so a refused opening removed them without reporting their bytes.
#[test]
fn a_refused_request_changes_nothing() {
    let (_home, store) = enabled("records-refusals-nothing");
    let old = part(&store, "c-1", "old", small("stale draft"), false).unwrap();
    age(&store, "old", 86_401);
    let old_state = store_path(&store, "c-1", "old.state");
    let leftover = store_path(&store, "c-1", "new.jsonl");
    fs::write(&leftover, "a line a first request left\n").unwrap();
    let mut gapped = small("new");
    gapped[0].seq = 5;
    let mut invalid = small("new");
    invalid[0].size = 0;
    let over = Write {
        draft: "new".into(),
        records: small("new"),
        turn_bytes: Some(2 << 30),
        ..Write::default()
    };
    let refusals = [
        part(&store, "c-1", "new", gapped, false).map_err(reason),
        part(&store, "c-1", "new", invalid, false).map_err(reason),
        store.write_records("c-1", over, &LIMITS).map_err(reason),
    ];
    for refused in refusals {
        assert!(refused.is_err(), "{refused:?}");
        assert!(old_state.exists(), "the stale draft is still there");
        assert!(leftover.exists(), "and the leftover file");
    }
    let opened = part(&store, "c-1", "new", small("new"), false).unwrap();
    assert_eq!(opened.freed_bytes, old.stored_bytes);
    assert!(!old_state.exists());
    let sealed = part(&store, "c-1", "new", Vec::new(), true).unwrap();
    assert_eq!(
        sealed.turn.unwrap().records,
        1,
        "the leftover line is gone (M8)"
    );
    let new = messages(&[json!({"role": "user", "content": "new"})]);
    assert_eq!(spelled(&turns(&store, "c-1")[0].messages), spelled(&new));
}

/// Finding 6: a seal compares the draft's bytes with
/// `NEOSIAN_MAX_RECORD_TURN_BYTES`, as item 23 says, so a bound lowered
/// while a draft was open refuses its seal; the draft stays open.
#[test]
fn the_seal_compares_the_draft_with_the_stores_bound() {
    let (_home, store) = enabled("records-refusals-bound");
    for chunk in parts(&records_of(&example()).unwrap(), 300_000) {
        part(&store, "c-1", "d", chunk, false).unwrap();
    }
    let lowered = limits::Records {
        max_record_turn_bytes: 65_536,
        ..LIMITS
    };
    let seal = Write {
        draft: "d".into(),
        seal: true,
        ..Write::default()
    };
    let refused = store
        .write_records("c-1", seal, &lowered)
        .unwrap_err()
        .text();
    assert_eq!(
        refused,
        "resource limit NEOSIAN_MAX_RECORD_TURN_BYTES exceeded: 812400 > 65536; this store does not keep a turn this large"
    );
    assert!(turns(&store, "c-1").is_empty());
    let probe = part(&store, "c-1", "d", Vec::new(), false).unwrap();
    assert_eq!((probe.state, probe.next_seq), (State::Open, 16));
    assert_eq!(
        part(&store, "c-1", "d", Vec::new(), true).unwrap().state,
        State::Sealed
    );
}

/// Finding 9: one unreadable state refused every new draft beside it. It
/// now counts as a draft untouched since its file's time, cleaned like one
/// and its bytes reported from its records file.
#[test]
fn an_unreadable_draft_state_blocks_no_other_draft() {
    let (_home, store) = enabled("records-refusals-unreadable");
    let bad = part(&store, "c-1", "bad", small("x"), false).unwrap();
    let state = store_path(&store, "c-1", "bad.state");
    fs::write(&state, "{").unwrap();
    let fresh = part(&store, "c-1", "fresh", small("y"), true).unwrap();
    let bookkeeping = Sizes::new("c-1", "fresh").bookkeeping();
    assert_eq!(
        (fresh.state, fresh.freed_bytes),
        (State::Sealed, bookkeeping)
    );
    assert!(state.exists(), "not stale yet");
    let refused = part(&store, "c-1", "bad", small("x"), false)
        .unwrap_err()
        .text();
    assert!(
        refused.contains("a draft's state is unreadable"),
        "{refused}"
    );
    let past = SystemTime::now() - Duration::from_secs(86_401);
    fs::File::options()
        .write(true)
        .open(&state)
        .unwrap()
        .set_modified(past)
        .unwrap();
    let other = part(&store, "c-1", "other", small("z"), false).unwrap();
    assert_eq!(other.freed_bytes, bad.stored_bytes);
    assert!(!state.exists() && !store_path(&store, "c-1", "bad.jsonl").exists());
}

/// Mutation M11: a draft untouched for exactly `NEOSIAN_DRAFT_SECONDS` is
/// kept; one microsecond more and it is stale.
#[test]
fn a_draft_is_stale_only_past_its_seconds() {
    let (_home, store) = enabled("records-refusals-stale");
    part(&store, "c-1", "d", small("x"), false).unwrap();
    let state: Value =
        serde_json::from_str(&fs::read_to_string(store_path(&store, "c-1", "d.state")).unwrap())
            .unwrap();
    let touched = Stamp::parse(state["touched_at"].as_str().unwrap())
        .unwrap()
        .instant();
    let drafts = store.conversation_dir("c-1").unwrap().join(".drafts");
    let at = |now: i64| stale(&store.root, &drafts, 60, now, "c-1").unwrap().len();
    assert_eq!(at(touched + 60_000_000), 0);
    assert_eq!(at(touched + 60_000_001), 1);
}

/// Mutation M5: lines past the state's `file_bytes`, left by a request cut
/// off before its state was written, are dropped by the next request.
#[test]
fn lines_past_the_state_are_dropped_by_the_next_request() {
    let (_home, store) = enabled("records-refusals-cut");
    let records = records_of(&example()).unwrap();
    part(&store, "c-1", "d", records[..8].to_vec(), false).unwrap();
    let file = store_path(&store, "c-1", "d.jsonl");
    let kept = fs::read_to_string(&file).unwrap();
    let mut cut = kept.clone();
    cut.push_str(&super::super::drafts::line(&records[8]));
    cut.push_str("{\"half a line");
    fs::write(&file, cut).unwrap();
    part(&store, "c-1", "d", records[8..].to_vec(), true).unwrap();
    assert_eq!(
        spelled(&turns(&store, "c-1")[0].messages),
        spelled(&example())
    );
}

/// Mutation M9: after a seal a repeat is still compared by digest.
#[test]
fn a_changed_repeat_after_a_seal_is_a_mismatch() {
    let (_home, store) = enabled("records-refusals-repeat");
    let records = records_of(&example()).unwrap();
    let chunks = parts(&records, 300_000);
    let count = chunks.len();
    for (at, chunk) in chunks.into_iter().enumerate() {
        part(&store, "c-1", "d", chunk, at + 1 == count).unwrap();
    }
    let mut changed = records[1].clone();
    changed.text = "{\"command\":\"make tess\"}".into();
    assert_eq!(
        part(&store, "c-1", "d", vec![changed], false)
            .map_err(reason)
            .unwrap_err(),
        "draft_mismatch"
    );
    assert_eq!(
        part(&store, "c-1", "d", records[1..2].to_vec(), false)
            .unwrap()
            .state,
        State::Sealed
    );
}

/// Ruling R19: a probe is not a touch. It leaves the state's bytes as they
/// are, so a stale draft stays stale; a request carrying records, a pure
/// repeat included, touches. A probe still drops lines past the state.
#[test]
fn a_probe_leaves_the_draft_untouched_and_a_repeat_touches_it() {
    let (_home, store) = enabled("records-refusals-probe");
    part(&store, "c-1", "d", small("x"), false).unwrap();
    age(&store, "d", 3_600);
    let path = store_path(&store, "c-1", "d.state");
    let aged = fs::read_to_string(&path).unwrap();
    let file = store_path(&store, "c-1", "d.jsonl");
    let kept = fs::read_to_string(&file).unwrap();
    fs::write(&file, format!("{kept}{{\"half a line")).unwrap();
    let probe = part(&store, "c-1", "d", Vec::new(), false).unwrap();
    assert_eq!((probe.state, probe.next_seq), (State::Open, 1));
    assert_eq!((probe.stored_bytes, probe.freed_bytes), (0, 0));
    assert_eq!(fs::read_to_string(&path).unwrap(), aged);
    assert_eq!(fs::read_to_string(&file).unwrap(), kept);
    let drafts = store.conversation_dir("c-1").unwrap().join(".drafts");
    let now = Stamp::now().instant();
    let old = |seconds| stale(&store.root, &drafts, seconds, now, "c-1").unwrap();
    assert_eq!(old(60).len(), 1);
    let repeat = part(&store, "c-1", "d", small("x"), false).unwrap();
    assert_eq!((repeat.next_seq, repeat.stored_bytes), (1, 0));
    assert_ne!(fs::read_to_string(&path).unwrap(), aged);
    assert_eq!(old(60).len(), 0);
}
