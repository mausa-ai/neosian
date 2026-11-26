//! Closing a span into the queue, numbering, holding and the lock.

use std::fs::{self, File};
use std::io;
use std::time::{Duration, SystemTime};

use super::super::draft::{draft_id, first_line, is_opening};
use super::super::queue::Queue;
use super::super::state::{POLICY_CHANGED, Reason, State};
use super::{ID, spool, state};
use crate::testing::Scratch;

pub(super) const SPAN: &str =
    "{\"kind\": \"prompt\", \"text\": \"yes\"}\n{\"kind\": \"stop\", \"text\": \"Done.\"}\n";

/// An open span of `ID` holding `bytes` under `base`'s spool.
pub(super) fn open(base: &std::path::Path, bytes: impl AsRef<[u8]>) -> std::path::PathBuf {
    let span = spool(base).join(format!("{ID}.jsonl"));
    fs::create_dir_all(spool(base)).unwrap();
    fs::write(&span, bytes).unwrap();
    span
}

pub(super) fn close(queue: &Queue, span: &std::path::Path) -> io::Result<u64> {
    let bytes = fs::read(span)?;
    queue.close(span, &bytes, ID, None, state, || Ok(()))
}

#[test]
fn the_state_is_written_before_the_rename_and_a_cut_leaves_only_a_state() {
    let scratch = Scratch::new("landing-cut");
    let span = open(scratch.path(), SPAN);
    let queue = Queue::new(&spool(scratch.path()));
    let pending = queue.pending_dir(ID);
    let cut = queue.close(&span, SPAN.as_bytes(), ID, None, state, || {
        Err(io::Error::other("cut"))
    });
    assert_eq!(cut.unwrap_err().to_string(), "cut");
    assert!(pending.join("1.state").is_file());
    assert!(!pending.join("1.jsonl").exists());
    let kept = fs::read_to_string(&span).unwrap();
    assert!(is_opening(first_line(&kept)) && kept.ends_with(SPAN));
    // A state younger than a minute may be a close in progress: kept, never listed.
    assert!(queue.pending(ID).unwrap().is_empty());
    assert!(pending.join("1.state").is_file());
    let old = SystemTime::now() - Duration::from_secs(120);
    File::options()
        .write(true)
        .open(pending.join("1.state"))
        .unwrap()
        .set_modified(old)
        .unwrap();
    assert!(queue.pending(ID).unwrap().is_empty());
    assert!(!pending.join("1.state").exists());
    // The next close keeps the opening line the cut left, once.
    assert_eq!(close(&queue, &span).unwrap(), 1);
    let closed = fs::read_to_string(pending.join("1.jsonl")).unwrap();
    assert_eq!(closed, kept);
    let written = State::read(&pending.join("1.state")).unwrap();
    assert_eq!(written.draft, draft_id(ID, first_line(&closed)));
    assert_eq!(written.bytes, closed.len() as u64);
    assert_eq!(queue.pending(ID).unwrap(), [1]);
    assert!(!span.exists());
}

#[test]
fn a_span_without_an_opening_line_is_given_one_at_close() {
    let scratch = Scratch::new("landing-opening");
    let queue = Queue::new(&spool(scratch.path()));
    let span = open(scratch.path(), SPAN);
    assert_eq!(close(&queue, &span).unwrap(), 1);
    let closed = fs::read_to_string(queue.pending_dir(ID).join("1.jsonl")).unwrap();
    let (line, rest) = closed.split_once('\n').unwrap();
    assert!(is_opening(line) && rest == SPAN);
    let opening: serde_json::Value = serde_json::from_str(line).unwrap();
    let keys: Vec<&String> = opening.as_object().unwrap().keys().collect();
    assert_eq!(keys, ["kind", "opened_at", "nonce"]);
    let nonce = uuid::Uuid::parse_str(opening["nonce"].as_str().unwrap()).unwrap();
    assert_eq!(nonce.get_version_num(), 4);
    let opened = opening["opened_at"].as_str().unwrap();
    assert!(opened.ends_with('Z') && opened.len() == "2026-11-26T12:00:00.000000Z".len());
    assert_eq!(rest.lines().filter(|l| is_opening(l)).count(), 0);
}

#[test]
fn the_bytes_after_the_opening_line_are_the_span_s_own() {
    let scratch = Scratch::new("landing-bytes");
    let queue = Queue::new(&spool(scratch.path()));
    let mut bytes = SPAN.as_bytes().to_vec();
    bytes.extend_from_slice(b"{\"kind\": \"prompt\", \"text\": \"\xff\xfe\"}\r\n");
    let span = open(scratch.path(), &bytes);
    assert_eq!(close(&queue, &span).unwrap(), 1);
    let closed = fs::read(queue.pending_dir(ID).join("1.jsonl")).unwrap();
    let at = closed.iter().position(|b| *b == b'\n').unwrap();
    assert_eq!(&closed[at + 1..], &bytes[..]);
    let written = State::read(&queue.pending_dir(ID).join("1.state")).unwrap();
    assert_eq!(written.bytes, closed.len() as u64);
}

#[test]
fn n_stays_unique_across_pending_and_held() {
    let scratch = Scratch::new("landing-numbers");
    let queue = Queue::new(&spool(scratch.path()));
    let held = queue.held_dir(ID);
    fs::create_dir_all(&held).unwrap();
    for part in ["3.jsonl", "3.state", "3.reason"] {
        fs::write(held.join(part), "{}").unwrap();
    }
    assert_eq!(queue.next_n(ID).unwrap(), 4);
    assert_eq!(close(&queue, &open(scratch.path(), SPAN)).unwrap(), 4);
    assert_eq!(close(&queue, &open(scratch.path(), SPAN)).unwrap(), 5);
    queue.hold(ID, 4, &Reason::of(POLICY_CHANGED)).unwrap();
    assert_eq!(queue.pending(ID).unwrap(), [5]);
    assert_eq!(close(&queue, &open(scratch.path(), SPAN)).unwrap(), 6);
    assert_eq!(queue.pending(ID).unwrap(), [5, 6]);
    // Leading zeros and other names are not numbers.
    fs::write(queue.pending_dir(ID).join("07.jsonl"), "").unwrap();
    fs::write(queue.pending_dir(ID).join("9.tmp"), "").unwrap();
    assert_eq!(queue.next_n(ID).unwrap(), 7);
}

#[test]
fn a_hold_keeps_the_number_the_state_and_the_reason() {
    let scratch = Scratch::new("landing-hold");
    let queue = Queue::new(&spool(scratch.path()));
    close(&queue, &open(scratch.path(), SPAN)).unwrap();
    let state = fs::read(queue.pending_dir(ID).join("1.state")).unwrap();
    queue.hold(ID, 1, &Reason::of(POLICY_CHANGED)).unwrap();
    let held = queue.held_dir(ID);
    assert_eq!(fs::read(held.join("1.state")).unwrap(), state);
    assert_eq!(
        fs::read_to_string(held.join("1.reason")).unwrap(),
        "{\"reason\":\"policy_changed\"}\n"
    );
    assert!(held.join("1.jsonl").is_file());
    let left: Vec<_> = fs::read_dir(queue.pending_dir(ID)).unwrap().collect();
    assert!(left.is_empty(), "{left:?}");
    assert!(!queue.has_pending(ID).unwrap());
}

#[cfg(unix)]
#[test]
fn the_lock_is_exclusive_and_a_tidy_leaves_nothing() {
    let scratch = Scratch::new("landing-lock");
    let queue = Queue::new(&spool(scratch.path()));
    let lock = queue.try_lock(ID).unwrap().expect("a free lock");
    assert!(queue.try_lock(ID).unwrap().is_none());
    let stale = File::open(queue.pending_dir(ID).join(".lock")).unwrap();
    super::super::lock::tidy(lock).unwrap();
    assert!(!queue.pending_dir(ID).exists());
    // A handle to the unlinked lock file locks nothing a new opener sees.
    stale.try_lock().unwrap();
    let fresh = queue.try_lock(ID).unwrap().expect("a new lock file");
    // A tidy leaves a directory that still holds a turn.
    close(&queue, &open(scratch.path(), SPAN)).unwrap();
    super::super::lock::tidy(fresh).unwrap();
    assert_eq!(queue.pending(ID).unwrap(), [1]);
}
