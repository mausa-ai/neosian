//! A pending turn is checked before it is sent (ruling E5), what cuts
//! leave is swept (E7), and a cut inside a hold leaves the turn landable.

use std::fs::{self, File};
use std::io;
use std::path::Path;
use std::time::{Duration, SystemTime};

use serde_json::Value;

use super::super::queue::Queue;
use super::super::state::{POLICY_CHANGED, Reason};
use super::close::{SPAN, close, open};
use super::land::{prompts, turn};
use super::{ID, settings, spool};
use crate::record::settings::Settings;
use crate::testing::Scratch;

/// A pending turn 1 of `ID`, closed while the root could not be written.
fn queued(base: &Path) -> (Settings, Queue) {
    let settings = settings(base, None);
    let root = base.join("store");
    fs::write(&root, "not a directory").unwrap();
    assert!(turn(&settings, "first").is_err());
    fs::remove_file(&root).unwrap();
    let queue = Queue::new(&spool(base));
    assert_eq!(queue.pending(ID).unwrap(), [1]);
    (settings, queue)
}

/// The next turn lands as turn 1 and pending turn 1 is held `unreadable`
/// for a reason whose message contains `why`.
fn held_for(settings: &Settings, queue: &Queue, why: &str) {
    assert_eq!(turn(settings, "after").unwrap().turn, Some(1));
    assert_eq!(prompts(&settings_root(settings)), ["after"]);
    let held = queue.held_dir(ID);
    let reason: Value =
        serde_json::from_str(&fs::read_to_string(held.join("1.reason")).unwrap()).unwrap();
    assert_eq!(reason["reason"], "unreadable");
    let message = reason["message"].as_str().unwrap();
    assert!(message.contains(why), "{message}");
    assert!(held.join("1.jsonl").is_file());
}

fn settings_root(settings: &Settings) -> std::path::PathBuf {
    match &settings.store {
        crate::remote::selection::Selection::File(root) => root.clone(),
        _ => unreachable!("the tests land on a FileStore"),
    }
}

fn backdate(path: &Path) {
    let old = SystemTime::now() - Duration::from_secs(120);
    File::options()
        .write(true)
        .open(path)
        .unwrap()
        .set_modified(old)
        .unwrap();
}

#[test]
fn a_turn_whose_length_differs_from_its_state_is_held() {
    let scratch = Scratch::new("landing-length");
    let (settings, queue) = queued(scratch.path());
    fs::write(queue.pending_dir(ID).join("1.jsonl"), "").unwrap();
    held_for(&settings, &queue, "the turn holds 0 bytes, its state ");
}

#[test]
fn a_turn_without_its_opening_line_is_held() {
    let scratch = Scratch::new("landing-no-opening");
    let (settings, queue) = queued(scratch.path());
    let pending = queue.pending_dir(ID);
    let text = fs::read_to_string(pending.join("1.jsonl")).unwrap();
    let rest = text.split_once('\n').unwrap().1;
    // The same length, so only the missing opening line is wrong.
    let padded = format!("{rest}{}", "\n".repeat(text.len() - rest.len()));
    fs::write(pending.join("1.jsonl"), padded).unwrap();
    held_for(&settings, &queue, "the turn has no opening line");
}

#[test]
fn a_turn_whose_state_is_missing_is_held() {
    let scratch = Scratch::new("landing-no-state");
    let (settings, queue) = queued(scratch.path());
    fs::remove_file(queue.pending_dir(ID).join("1.state")).unwrap();
    held_for(&settings, &queue, "(os error 2)");
    assert!(!queue.held_dir(ID).join("1.state").exists());
}

#[test]
fn a_turn_whose_state_names_another_conversation_is_held() {
    let scratch = Scratch::new("landing-other-state");
    let (settings, queue) = queued(scratch.path());
    let file = queue.pending_dir(ID).join("1.state");
    let text = fs::read_to_string(&file).unwrap();
    let changed = text.replace(
        &format!("\"conversation_id\":\"{ID}\""),
        "\"conversation_id\":\"another\"",
    );
    assert_ne!(changed, text);
    fs::write(&file, changed).unwrap();
    held_for(&settings, &queue, "the state names another conversation");
}

#[test]
fn what_cuts_leave_in_both_directories_is_swept_once_a_minute_old() {
    let scratch = Scratch::new("landing-sweep");
    let queue = Queue::new(&spool(scratch.path()));
    let (pending, held) = (queue.pending_dir(ID), queue.held_dir(ID));
    fs::create_dir_all(&pending).unwrap();
    fs::create_dir_all(&held).unwrap();
    let leftovers = [
        held.join("3.state"),
        held.join("3.reason"),
        held.join(".4.state.copy"),
        pending.join(".neosian-tmp-1-0000abcd"),
        held.join(".neosian-tmp-2-0000abcd"),
        pending.join("6.state"),
    ];
    let kept = [
        held.join("5.jsonl"),
        held.join("5.state"),
        held.join("5.reason"),
        pending.join(".lock"),
        pending.join(".x.state.copy"),
    ];
    for path in leftovers.iter().chain(&kept) {
        fs::write(path, "{}").unwrap();
    }
    assert!(queue.pending(ID).unwrap().is_empty());
    assert!(
        leftovers.iter().all(|path| path.exists()),
        "a fresh leftover is kept"
    );
    for path in leftovers.iter().chain(&kept) {
        backdate(path);
    }
    assert!(queue.pending(ID).unwrap().is_empty());
    for path in &leftovers {
        assert!(!path.exists(), "{}", path.display());
    }
    assert!(kept.iter().all(|path| path.exists()));
}

#[test]
fn a_cut_inside_a_hold_leaves_the_turn_pending_and_a_later_hold_completes() {
    let scratch = Scratch::new("landing-hold-cut");
    let queue = Queue::new(&spool(scratch.path()));
    close(&queue, &open(scratch.path(), SPAN)).unwrap();
    let reason = Reason::of(POLICY_CHANGED);
    let cut = queue.hold_with(ID, 1, &reason, || Err(io::Error::other("cut")));
    assert_eq!(cut.unwrap_err().to_string(), "cut");
    let (pending, held) = (queue.pending_dir(ID), queue.held_dir(ID));
    assert!(held.join("1.reason").is_file() && held.join("1.state").is_file());
    assert!(!held.join("1.jsonl").exists());
    assert_eq!(queue.pending(ID).unwrap(), [1]);
    assert!(pending.join("1.state").is_file());
    assert_eq!(queue.next_n(ID).unwrap(), 2);
    backdate(&held.join("1.reason"));
    backdate(&held.join("1.state"));
    assert_eq!(queue.pending(ID).unwrap(), [1]);
    assert!(!held.join("1.reason").exists() && !held.join("1.state").exists());
    queue.hold(ID, 1, &reason).unwrap();
    assert!(queue.pending(ID).unwrap().is_empty());
    for part in ["1.jsonl", "1.state", "1.reason"] {
        assert!(held.join(part).is_file(), "{part}");
    }
}
