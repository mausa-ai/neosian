//! Sweep crash recovery under an ordinary read, including restoration-only IDs.
use std::fs;

use super::{LIMITS, age, commit, draft, draft_bytes, enabled, restore, row};
use crate::conversation::turn_records::Staged;
use crate::store::FileStore;
use serde_json::json;

#[test]
fn sweep_settles_an_interrupted_commit_before_removing_old_staging() {
    use crate::store::records::commit::STOP;
    let (_home, store) = enabled("records-sweep-commit");
    let state = restore(&store, "c");
    let request = commit();
    STOP.with(|n| n.set(1));
    let stopped = store.restore_records("c", request.clone(), &LIMITS);
    STOP.with(|n| n.set(0));
    assert!(
        stopped
            .unwrap_err()
            .text()
            .contains("stopped after restore step")
    );
    age(&state);
    let page = store.sweep_records(1, None, true).unwrap();
    assert!(page.conversations.is_empty() && page.next_cursor.is_none());
    assert_eq!(store.read_turns("c", 0, None).unwrap().len(), 1);
    let replay = store.restore_records("c", request, &LIMITS).unwrap();
    assert_eq!(replay.state, Staged::Committed);
}

#[test]
fn an_interrupted_discard_finishes_the_selected_conversation_on_reopen() {
    use crate::store::records::sweep::STOP;
    for cut in [1, 2, 3] {
        let (home, store) = enabled("records-sweep-recovery");
        let staged = restore(&store, "c");
        let stale = draft(&store, "c", "old");
        let fresh = draft(&store, "c", "keep");
        let later = draft(&store, "z", "later");
        for path in [&staged, &stale, &later] {
            age(path);
        }
        STOP.with(|n| n.set(cut));
        let halted = store.sweep_records(1, None, true);
        STOP.with(|n| n.set(0));
        assert!(halted.is_err(), "cut {cut}");
        drop(store);
        let store = FileStore::open(home.path()).unwrap();
        // A normal read settles the selected transaction; no second sweep is needed.
        assert_eq!(store.last_turn_number("c").unwrap(), 0);
        assert!(!staged.parent().unwrap().exists() && !stale.exists());
        assert!(!stale.with_extension("jsonl").exists());
        assert!(fresh.exists() && later.exists());
        let page = store.sweep_records(1, None, true).unwrap();
        assert_eq!(
            json!(page.conversations),
            json!([row("z", 1, 0, draft_bytes("z", "later"))])
        );
    }
}

#[test]
fn restore_only_discards_preserve_the_original_conversation_directory_layout() {
    use crate::store::records::sweep::STOP;
    for existed in [false, true] {
        for cut in [0, 1, 2, 3] {
            let (home, store) = enabled("records-sweep-restore-only");
            let directory = store.conversation_dir("staged").unwrap();
            if existed {
                fs::create_dir_all(&directory).unwrap();
            }
            let state = restore(&store, "staged");
            age(&state);
            assert_eq!(directory.exists(), existed);
            STOP.with(|n| n.set(cut));
            let result = store.sweep_records(1, None, true);
            STOP.with(|n| n.set(0));
            assert_eq!(result.is_ok(), cut == 0, "cut {cut}");
            drop(store);
            let store = FileStore::open(home.path()).unwrap();
            assert_eq!(store.last_turn_number("staged").unwrap(), 0);
            assert!(!state.parent().unwrap().exists());
            assert_eq!(
                directory.exists(),
                existed,
                "restore-only discard at cut {cut}"
            );
            if existed {
                assert_eq!(fs::read_dir(directory).unwrap().count(), 0);
            }
        }
    }
}
