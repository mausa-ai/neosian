//! A seal whose header append was torn (finding 2 of the T1 review): the
//! marker holds the log's length before the append, so recovery cuts a
//! partial header line back, appends the header again and reads the whole
//! turn, wherever the line was cut. What lies past that length and is not
//! a partial write of the header is left as it is and refused. A marker of
//! one line, as the previous commit wrote it, still rolls forward.

use std::fs;
use std::path::PathBuf;

use serde_json::json;

use super::super::seal::CRASH_AFTER;
use super::{enabled, messages, part, turns};
use crate::conversation::turn_records::records_of;
use crate::store::FileStore;
use crate::testing::Scratch;

/// A store and its home holding one whole turn of `c-1`, its log ending in
/// a line ending when `ended`, and a seal of `d` stopped after `step`: the
/// log's bytes, the log's path and the seal marker's path.
fn stopped(name: &str, ended: bool, step: u8) -> (Scratch, FileStore, Vec<u8>, PathBuf, PathBuf) {
    let (home, store) = enabled(name);
    let first = messages(&[json!({"role": "user", "content": "first"})]);
    store.append_turn("c-1", first, None).unwrap();
    let directory = store.conversation_dir("c-1").unwrap();
    let log = directory.join("turns.jsonl");
    let mut original = fs::read(&log).unwrap();
    if !ended {
        original.pop();
        fs::write(&log, &original).unwrap();
    }
    let records = records_of(&messages(&[json!({"role": "user", "content": "only"})])).unwrap();
    CRASH_AFTER.with(|crash| crash.set(step));
    assert!(part(&store, "c-1", "d", records, true).is_err());
    CRASH_AFTER.with(|crash| crash.set(0));
    let marker = directory.join(".drafts").join("sealing");
    (home, store, original, log, marker)
}

#[test]
fn a_header_torn_at_any_byte_is_cut_back_and_appended_again() {
    for ended in [true, false] {
        let (_home, store, original, log, marker) = stopped(&format!("torn-{ended}"), ended, 3);
        let intent = fs::read_to_string(&marker).unwrap();
        let header = intent.lines().next().unwrap();
        let separator = if ended { "" } else { "\n" };
        let intended = format!("{separator}{header}\n").into_bytes();
        for cut in 0..=intended.len() {
            fs::write(&log, [&original[..], &intended[..cut]].concat()).unwrap();
            fs::write(&marker, &intent).unwrap();
            let read = turns(&store, "c-1");
            assert_eq!(read.len(), 2, "ended {ended}, cut at byte {cut}");
            assert_eq!(read[1].turn, 2);
            let whole = [&original[..], &intended[..]].concat();
            assert_eq!(fs::read(&log).unwrap(), whole, "ended {ended}, cut {cut}");
            assert!(!marker.exists());
        }
    }
}

#[test]
fn a_suffix_that_is_not_the_header_is_left_and_refused() {
    let (_home, store, original, log, marker) = stopped("torn-foreign", true, 3);
    let foreign = [&original[..], b"{\"neosian_format\": 9"].concat();
    fs::write(&log, &foreign).unwrap();
    let refused = store.read_turns("c-1", 0, None).unwrap_err().text();
    assert!(refused.contains("c-1"), "{refused}");
    assert_eq!(fs::read(&log).unwrap(), foreign);
    assert!(marker.exists());
}

#[test]
fn a_marker_without_the_log_length_rolls_forward_as_before() {
    let (_home, store, original, log, marker) = stopped("torn-old-marker", true, 2);
    let intent = fs::read_to_string(&marker).unwrap();
    let header = intent.lines().next().unwrap();
    fs::write(&marker, format!("{header}\n")).unwrap();
    assert_eq!(turns(&store, "c-1").len(), 2);
    let whole = [&original[..], format!("{header}\n").as_bytes()].concat();
    assert_eq!(fs::read(&log).unwrap(), whole);
}
