//! Ruling D5 with a pause: a span marked `.paused` is discarded, never
//! held, when a policy change is seen (ruling E6).

use std::fs;
use std::path::Path;

use super::super::changed::{Recorder, guarded};
use crate::testing::Scratch;

const KEY: &str = "29d58b67856cebece6d32bd4459f752da27de5f33f300638842b57d414ab0440";
const PAUSED: &str = "w-11111111222243338444555555555555";

fn span(directory: &Path, workspace: &str, text: &str) -> String {
    let id = format!("{workspace}-{KEY}");
    fs::write(directory.join(format!("{id}.jsonl")), text).unwrap();
    id
}

fn recorder() -> Recorder<'static> {
    Recorder {
        agent: "codex",
        native: "native-session",
        parent: None,
    }
}

#[test]
fn a_paused_span_is_discarded_and_an_unpaused_one_held() {
    for paused in [true, false] {
        let scratch = Scratch::new(&format!("landing-paused-{paused}"));
        let directory = scratch.path();
        let id = span(
            directory,
            PAUSED,
            "{\"kind\": \"prompt\", \"text\": \"private\"}\n",
        );
        if paused {
            fs::write(directory.join(format!("{id}.paused")), "").unwrap();
        }
        let guard = directory.join(format!("{KEY}.policy"));
        fs::write(&guard, "old").unwrap();
        // The new fingerprint, as another workspace's event brings it.
        assert_eq!(
            guarded(&guard, KEY, "new", true, &recorder()).unwrap(),
            None
        );
        assert!(!guard.exists());
        assert!(!directory.join(format!("{id}.jsonl")).exists());
        let held = directory.join("held").join(&id);
        assert_eq!(held.join("1.jsonl").is_file(), !paused);
        if paused {
            assert!(!held.exists());
            assert!(directory.join(format!("{id}.paused")).exists());
        }
    }
}

#[test]
fn the_same_fingerprint_or_an_event_that_records_nothing_keeps_the_guard() {
    let scratch = Scratch::new("landing-guard-kept");
    let directory = scratch.path();
    let id = span(
        directory,
        PAUSED,
        "{\"kind\": \"prompt\", \"text\": \"kept\"}\n",
    );
    let guard = directory.join(format!("{KEY}.policy"));
    fs::write(&guard, "old").unwrap();
    let kept = guarded(&guard, KEY, "old", true, &recorder()).unwrap();
    assert_eq!(kept.as_deref(), Some("old"));
    let kept = guarded(&guard, KEY, "new", false, &recorder()).unwrap();
    assert_eq!(kept.as_deref(), Some("old"));
    assert!(directory.join(format!("{id}.jsonl")).is_file());
    assert!(!directory.join("held").exists());
}
