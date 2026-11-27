//! A transfer's trail pages on a FileStore: storage order through equal and
//! earlier times across page boundaries, positions checked, rows refused.

use super::*;
use crate::testing::Scratch;

const ID: &str = "trail-session";
const TRAIL: &str = "conversations/trail-session/redactions.jsonl";

fn row(at: &str, actor: &str, turns: &[u64]) -> String {
    format!(
        "{{\"neosian_format\":1,\"turns\":{turns:?},\"actor\":\"{actor}\",\"created_at\":\"2026-08-19T10:00:{at}Z\"}}\n"
    )
}

fn planted(name: &str, text: &str) -> (Scratch, FileStore) {
    let home = Scratch::new(name);
    home.plant(TRAIL, text);
    let store = FileStore::open(home.path()).unwrap();
    (home, store)
}

fn actors(acts: &[Redaction]) -> Vec<&str> {
    acts.iter()
        .map(|act| act.actor.as_deref().unwrap())
        .collect()
}

#[test]
fn pages_keep_storage_order_through_ties_and_earlier_times() {
    // b and c share a time across the first boundary, d and e across the
    // second; times go backwards twice; f has no line ending.
    let rows = [
        row("05", "a", &[1]),
        row("03", "b", &[2]),
        row("03", "c", &[1, 2]),
        row("01", "d", &[3]),
        row("01", "e", &[3]),
        row("09", "f", &[4]).trim_end().to_string(),
    ];
    let (_home, store) = planted("trail-order", &rows.concat());
    let (mut at, mut seen, mut pages) = (TrailAt::default(), Vec::new(), Vec::new());
    loop {
        let (acts, next) = store.trail_page(ID, at, 2).unwrap();
        pages.push(actors(&acts).join(""));
        seen.extend(acts);
        let Some(next) = next else { break };
        let ends = rows[..next.rows as usize].concat().len() as u64;
        assert_eq!(next.offset, ends, "a page ends at a row start");
        at = next;
    }
    assert_eq!(pages, ["ab", "cd", "ef"]);
    assert_eq!(actors(&seen), ["a", "b", "c", "d", "e", "f"]);
    let times: Vec<String> = seen.iter().map(|act| act.created_at.z()).collect();
    assert_eq!(times[1], times[2]);
    assert_eq!(times[3], times[4]);
    assert_eq!(seen[2].turns, [1, 2]);
    // The audit read is unchanged: newest first by time, ties by row.
    let audit = store.turn_redactions(None, None, 3).unwrap();
    assert_eq!(actors(&audit), ["f", "a", "c"]);
}

#[test]
fn a_full_last_page_ends_the_read_without_another() {
    let text = [row("01", "a", &[1]), row("01", "b", &[1])].concat();
    let (_home, store) = planted("trail-full", &text);
    let (acts, next) = store.trail_page(ID, TrailAt::default(), 2).unwrap();
    assert_eq!((actors(&acts), next), (vec!["a", "b"], None));
    let (acts, next) = store.trail_page(ID, TrailAt::default(), 1).unwrap();
    let after = TrailAt {
        offset: row("01", "a", &[1]).len() as u64,
        rows: 1,
    };
    assert_eq!((actors(&acts), next), (vec!["a"], Some(after)));
}

#[test]
fn a_missing_trail_is_empty_only_from_its_start() {
    let home = Scratch::new("trail-missing");
    let store = FileStore::open(home.path()).unwrap();
    let (acts, next) = store.trail_page(ID, TrailAt::default(), 5).unwrap();
    assert!(acts.is_empty() && next.is_none());
    let later = TrailAt {
        offset: 10,
        rows: 1,
    };
    let refused = store.trail_page(ID, later, 5).unwrap_err().text();
    assert!(refused.contains("changed while it was read"), "{refused}");
    let zero = store.trail_page(ID, TrailAt::default(), 0).unwrap_err();
    assert_eq!(zero.text(), "limit must be >= 1, got 0");
}

#[test]
fn a_position_off_a_row_start_or_past_the_end_refuses() {
    let text = [row("01", "a", &[1]), row("02", "b", &[2])].concat();
    let (_home, store) = planted("trail-position", &text);
    let length = text.len() as u64;
    for offset in [3, length - 1, length + 1] {
        let at = TrailAt { offset, rows: 1 };
        let refused = store.trail_page(ID, at, 5).unwrap_err().text();
        assert!(refused.contains("changed while it was read"), "{refused}");
    }
    let (acts, next) = store
        .trail_page(
            ID,
            TrailAt {
                offset: length,
                rows: 2,
            },
            5,
        )
        .unwrap();
    assert!(acts.is_empty() && next.is_none());
}

#[test]
fn a_malformed_row_refuses_the_page_that_reaches_it_by_its_line() {
    let text = [
        row("01", "a", &[1]),
        row("02", "b", &[2]),
        row("03", "c", &[]),
        row("04", "d", &[4]),
    ]
    .concat();
    let (_home, store) = planted("trail-malformed", &text);
    let (acts, next) = store.trail_page(ID, TrailAt::default(), 2).unwrap();
    assert_eq!(actors(&acts), ["a", "b"]);
    let refused = store.trail_page(ID, next.unwrap(), 2).unwrap_err().text();
    assert!(refused.contains("redaction trail line 3"), "{refused}");
}
