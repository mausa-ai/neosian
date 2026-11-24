//! A seal repeated after its turn was folded (§40.54 items 9, 20 and 21,
//! T1b2c4, the ruling of 2026-11-24): the fold keeps the digest of every
//! record as the seal accepted it, so a repeat is confirmed in the parts
//! the client first sent, however they were split, a changed part is
//! `draft_mismatch`, and a receipt cut short is refused by name. The
//! receipt outlives a migration and a second fold of the same turn, and
//! goes, with the seal's own, when the turn is erased.

use std::fs;

use serde_json::json;

use super::super::migrating::{migrate, whole};
use super::super::{enabled, messages, part, reason};
use super::{fold, folded};
use crate::conversation::turn_records::{State, TurnRecord, records_of};

/// One prompt in two chunks, split where the default rule never would.
fn split() -> Vec<TurnRecord> {
    let whole = records_of(&messages(&[
        json!({"role": "user", "content": "hello world"}),
    ]));
    let whole = whole.unwrap().remove(0);
    let head = TurnRecord {
        text: "hello ".into(),
        ..whole.clone()
    };
    let tail = TurnRecord {
        seq: 1,
        chunk: 1,
        content: None,
        text: "world".into(),
        ..whole
    };
    vec![head, tail]
}

#[test]
fn a_seal_is_repeated_in_its_first_parts_after_a_fold_a_migration_and_a_second_fold() {
    let (home, store) = enabled("records-fold-receipt");
    let parts = split();
    let sealed = part(&store, "c-1", "d1", parts.clone(), true).unwrap();
    assert_eq!(sealed.turn.unwrap().turn, 1);
    let before = whole(&store, "c-1");
    let again = |records: Vec<TurnRecord>| part(&store, "c-1", "d1", records, false);
    let confirmed = |records: Vec<TurnRecord>| {
        let answer = again(records).unwrap();
        (answer.state, answer.turn.unwrap().turn, answer.next_seq)
    };
    let receipt = home.path().join("conversations/c-1/.drafts/folded/1");
    let mut evidence = Vec::new();
    for round in 0..3 {
        match round {
            0 => assert_eq!(fold(&store, "c-1"), folded("c-1", 1, 0, 0)),
            1 => assert_eq!(migrate(&store, "c-1").migrated, 1),
            _ => assert_eq!(fold(&store, "c-1"), folded("c-1", 1, 0, 0)),
        }
        assert_eq!(whole(&store, "c-1"), before, "round {round}");
        if round == 0 {
            evidence = fs::read(&receipt).unwrap();
        }
        assert_eq!(
            fs::read(&receipt).unwrap(),
            evidence,
            "the first fold's receipt"
        );
        for records in [Vec::new(), parts.clone(), parts[1..].to_vec()] {
            assert_eq!(confirmed(records), (State::Sealed, 1, 2), "round {round}");
        }
        let mut changed = parts.clone();
        changed[1].text = "wurld".into();
        assert_eq!(reason(again(changed).unwrap_err()), "draft_mismatch");
        // The same message split by the default rule is other records.
        let default = records_of(&messages(&[
            json!({"role": "user", "content": "hello world"}),
        ]));
        assert_eq!(
            reason(again(default.unwrap()).unwrap_err()),
            "draft_mismatch"
        );
        assert_eq!(store.last_turn_number("c-1").unwrap(), 1, "no second turn");
    }
    assert_eq!(evidence.iter().filter(|byte| **byte == b'\n').count(), 3);

    fs::write(&receipt, &evidence[..evidence.len() - 1]).unwrap();
    let short = again(parts[..1].to_vec()).unwrap_err().text();
    assert!(
        short.contains("turn 1: its folded receipt is unreadable"),
        "{short}"
    );
    fs::write(&receipt, &evidence).unwrap();
    assert_eq!(confirmed(parts[..1].to_vec()), (State::Sealed, 1, 2));
}

#[test]
fn erasing_a_folded_turn_removes_its_digests_and_its_seals_receipt() {
    for migrated in [false, true] {
        let (home, store) = enabled(&format!("records-fold-erase-{migrated}"));
        part(&store, "c-1", "d1", split(), true).unwrap();
        part(&store, "c-1", "d2", split(), true).unwrap();
        fold(&store, "c-1");
        if migrated {
            migrate(&store, "c-1");
        }
        let drafts = home.path().join("conversations/c-1/.drafts");
        assert!(drafts.join("folded/1").is_file() && drafts.join("d1.sealed").is_file());
        assert_eq!(
            store.redact_turns("c-1", None, Some(&[1]), None).unwrap(),
            1
        );
        assert!(
            !drafts.join("folded/1").exists(),
            "no digest outlives its turn"
        );
        assert!(!drafts.join("d1.sealed").exists());
        assert!(
            !home
                .path()
                .join("conversations/c-1/records/1.jsonl")
                .exists()
        );
        assert!(drafts.join("folded/2").is_file() && drafts.join("d2.sealed").is_file());
        let other = part(&store, "c-1", "d2", split(), false).unwrap();
        assert_eq!(
            other.state,
            State::Sealed,
            "the other seal is still repeated"
        );
        store.redact_turns("c-1", None, None, None).unwrap();
        assert!(!drafts.exists(), "erasing every turn removes them all");
    }
}
