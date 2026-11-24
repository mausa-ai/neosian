//! A migration stopped after each of its steps, the store opened again and
//! an ordinary call made (§40.54 item 21, T1b2c3): stopped before the
//! rename that publishes it, nothing changed and a rerun migrates; stopped
//! anywhere after, the next call on the conversation finishes the swap
//! before it answers. A staging directory a killed run left is removed,
//! and a published one on a store without records is refused by name.

use std::fs;

use serde_json::json;

use super::super::super::migrate::STOP;
use super::super::{LIMITS, enabled, example};
use super::{log, migrate, moved, say, settled, whole};
use crate::store::FileStore;
use crate::testing::Scratch;

/// Three whole turns, the second of sixteen records.
fn three(name: &str) -> (Scratch, FileStore) {
    let (home, store) = enabled(name);
    say(
        &store,
        "c-1",
        &[json!({"role": "user", "content": "first"})],
        None,
    );
    store.append_turn("c-1", example(), None).unwrap();
    say(
        &store,
        "c-1",
        &[json!({"role": "user", "content": "second"})],
        None,
    );
    (home, store)
}

fn stopped(store: &FileStore, step: u8) -> String {
    STOP.with(|at| at.set(step));
    let result = store.migrate_records("c-1", &LIMITS);
    STOP.with(|at| at.set(0));
    result.unwrap_err().text()
}

#[test]
fn a_migration_stopped_at_any_step_changes_nothing_or_is_finished_by_the_next_call() {
    for step in 1..=6_u8 {
        let (home, store) = three(&format!("records-migrate-stop-{step}"));
        let (original, before) = (fs::read(log(&store, "c-1")).unwrap(), whole(&store, "c-1"));
        let why = stopped(&store, step);
        assert!(
            why.contains(&format!("stopped after migrate step {step}")),
            "{why}"
        );
        drop(store);
        let store = FileStore::open(home.path()).unwrap();
        match step {
            1 | 4 => assert_eq!(store.last_turn_number("c-1").unwrap(), 3),
            2 | 5 => assert_eq!(store.conversations().unwrap(), ["c-1"]),
            _ => assert_eq!(
                store
                    .search_turns(&["second".into()], None, 5)
                    .unwrap()
                    .len(),
                1
            ),
        }
        assert!(settled(&store, "c-1"), "step {step}");
        assert_eq!(whole(&store, "c-1"), before, "step {step}");
        let now = fs::read(log(&store, "c-1")).unwrap();
        let records = store.conversation_dir("c-1").unwrap().join("records");
        if step == 1 {
            assert_eq!(now, original, "nothing was published");
            assert!(!records.exists());
            assert_eq!(migrate(&store, "c-1"), moved("c-1", 3, 0, 0));
        } else {
            assert_ne!(now, original, "step {step} was rolled forward");
            assert_eq!(migrate(&store, "c-1"), moved("c-1", 0, 3, 0));
            assert_eq!(fs::read(log(&store, "c-1")).unwrap(), now);
        }
        assert_eq!(fs::read_dir(&records).unwrap().count(), 3, "step {step}");
        say(
            &store,
            "c-1",
            &[json!({"role": "user", "content": "next"})],
            None,
        );
        assert_eq!(store.last_turn_number("c-1").unwrap(), 4);
        assert_eq!(whole(&store, "c-1")[..3], before[..]);
    }
}

#[test]
fn a_staging_directory_left_behind_is_removed_and_a_published_one_needs_records() {
    let (home, store) = three("records-migrate-left");
    let (original, before) = (fs::read(log(&store, "c-1")).unwrap(), whole(&store, "c-1"));
    let directory = store.conversation_dir("c-1").unwrap();
    for name in [".migrate-preparing", ".migrate-retired"] {
        fs::create_dir_all(directory.join(name).join("records")).unwrap();
        fs::write(directory.join(name).join("turns.jsonl"), "half a log").unwrap();
    }
    assert_eq!(whole(&store, "c-1"), before);
    assert!(settled(&store, "c-1"));
    assert_eq!(fs::read(log(&store, "c-1")).unwrap(), original);

    assert!(stopped(&store, 2).contains("stopped after migrate step 2"));
    let marker = home.path().join(".records/format");
    let text = fs::read(&marker).unwrap();
    fs::remove_file(&marker).unwrap();
    let refused = store.read_turns("c-1", 0, None).unwrap_err().text();
    assert!(
        refused.contains("a migration marker requires records to be enabled"),
        "{refused}"
    );
    assert_eq!(fs::read(log(&store, "c-1")).unwrap(), original);
    fs::write(&marker, text).unwrap();
    assert_eq!(whole(&store, "c-1"), before);
    assert_eq!(migrate(&store, "c-1"), moved("c-1", 0, 3, 0));
}
