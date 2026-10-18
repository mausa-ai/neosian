//! The store over a home planted byte for byte as the Python side writes
//! it (the fixtures are `FileStore`'s own output for the seed of
//! tests/differential/seed.py).

use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use super::*;
use crate::testing::Scratch;

const SCOPE: &str = "user:me/proj:demo";
const DIR: &str = "user%3Ame/proj%3Ademo";
const FACTS: &str = "---\nneosian_format: 1\nversion: 2\ncreated_at: '2026-10-10T18:20:18.273843Z'\nupdated_at: '2026-10-10T18:20:18.276022Z'\nactor: seed\n---\n# Facts\n\n- deploy target: eu-west-1 (v1)\n";
const SECRET: &str = "---\nneosian_format: 1\nversion: 1\ncreated_at: '2026-10-10T18:20:18.281441Z'\nupdated_at: '2026-10-10T18:20:18.281441Z'\nactor: seed\nredacted: true\n---\n";
const FACTS_ROWS: &str = concat!(
    r##"{"neosian_format":1,"path":"facts","version":1,"action":"created","content":"# Facts\n\n- deploy target: eu-west-1 (v0)\n","actor":"seed","created_at":"2026-10-10T18:20:18.273843Z","redacted":false}"##,
    "\n",
    r##"{"neosian_format":1,"path":"facts","version":2,"action":"modified","content":"# Facts\n\n- deploy target: eu-west-1 (v1)\n","actor":"seed","created_at":"2026-10-10T18:20:18.276022Z","redacted":false}"##,
    "\n",
);
const SCRATCH_ROWS: &str = concat!(
    r##"{"neosian_format":1,"path":"scratch","version":1,"action":"created","content":"# Scratch\n","actor":"seed","created_at":"2026-10-10T18:20:18.290210Z","redacted":false}"##,
    "\n",
    r##"{"neosian_format":1,"path":"scratch","version":2,"action":"deleted","content":"# Scratch\n","actor":"seed","created_at":"2026-10-10T18:20:18.291750Z","redacted":false}"##,
    "\n",
);

fn planted(name: &str) -> (Scratch, FileStore) {
    let home = Scratch::new(name);
    home.plant(&format!("{DIR}/documents/facts.md"), FACTS);
    home.plant(&format!("{DIR}/documents/secret.md"), SECRET);
    home.plant(
        &format!("{DIR}/documents/notes/reading.md"),
        &FACTS.replace("# Facts", "# Reading"),
    );
    home.plant(&format!("{DIR}/versions/facts.jsonl"), FACTS_ROWS);
    home.plant(&format!("{DIR}/versions/scratch.jsonl"), SCRATCH_ROWS);
    let store = FileStore::open(home.path()).unwrap();
    (home, store)
}

/// A refusal as the doors print it.
pub(super) fn refusal<T>(result: Result<T, Error>) -> String {
    match result {
        Err(Error::Memory(error)) => format!("[{}] {}", error.code, error.message),
        Err(Error::Value(sentence)) => sentence,
        Err(Error::Io(error)) => panic!("{error}"),
        Ok(_) => panic!("the call was answered"),
    }
}

#[test]
fn a_document_reads_back_byte_exact_and_a_missing_one_is_none() {
    let (_home, store) = planted("read");
    let facts = store.read(SCOPE, "facts").unwrap().unwrap();
    assert_eq!(
        facts.content,
        "# Facts\n\n- deploy target: eu-west-1 (v1)\n"
    );
    assert_eq!(
        (facts.envelope.version, facts.envelope.redacted),
        (2, false)
    );
    let secret = store.read(SCOPE, "secret").unwrap().unwrap();
    assert_eq!(
        (secret.content.as_str(), secret.envelope.redacted),
        ("", true)
    );
    assert!(store.read(SCOPE, "scratch").unwrap().is_none());
    assert!(store.read("user:nobody", "facts").unwrap().is_none());
}

#[test]
fn a_listing_is_the_live_documents_sorted_and_foreign_files_are_skipped() {
    let (home, store) = planted("list");
    home.plant(&format!("{DIR}/documents/Not Ours.md"), "no envelope");
    home.plant(&format!("{DIR}/documents/notes.txt"), "no envelope");
    let paths = |prefix| -> Vec<(String, bool)> {
        let entries = store.list_documents(SCOPE, prefix).unwrap();
        entries
            .into_iter()
            .map(|entry| (entry.path, entry.redacted))
            .collect()
    };
    let all = [("facts", false), ("notes/reading", false), ("secret", true)];
    assert_eq!(
        paths(""),
        all.map(|(path, redacted)| (path.to_string(), redacted))
    );
    assert_eq!(paths("notes/"), [("notes/reading".to_string(), false)]);
    assert_eq!(paths("nothing"), []);
    assert!(store.list_documents("user:nobody", "").unwrap().is_empty());
}

#[test]
fn versions_are_newest_first_and_outlive_the_document() {
    let (_home, store) = planted("versions");
    let numbers = |path, limit| -> Vec<(u64, &str)> {
        let rows = store.versions(SCOPE, path, limit).unwrap();
        rows.iter()
            .map(|row| (row.version, row.action.as_str()))
            .collect()
    };
    assert_eq!(numbers("facts", 50), [(2, "modified"), (1, "created")]);
    assert_eq!(numbers("facts", 1), [(2, "modified")]);
    assert_eq!(numbers("facts", 0), []);
    assert_eq!(numbers("scratch", 50), [(2, "deleted"), (1, "created")]);
    assert_eq!(numbers("never", 50), []);
}

#[test]
fn a_newer_format_or_a_broken_sidecar_is_refused() {
    let (home, store) = planted("refuse");
    home.plant(
        &format!("{DIR}/documents/future.md"),
        &FACTS.replace("format: 1", "format: 2"),
    );
    home.plant(
        &format!("{DIR}/versions/broken.jsonl"),
        "{\"neosian_format\":1}\nnot json\n",
    );
    assert_eq!(
        refusal(store.read(SCOPE, "future")),
        "[memory_format_unsupported] Unsupported memory format for 'future' in scope \
         'user:me/proj:demo': format 2 is newer than 1"
    );
    assert!(refusal(store.list_documents(SCOPE, "")).contains("'future'"));
    assert!(
        refusal(store.versions(SCOPE, "broken", 50)).ends_with("sidecar line 1 has invalid fields")
    );
}

#[test]
fn a_scope_or_a_path_outside_its_grammar_is_refused() {
    let (_home, store) = planted("grammar");
    assert_eq!(
        refusal(store.read("bad", "facts")),
        "[memory_scope_invalid] Invalid memory scope 'bad': does not match <type>:<id>[/...]"
    );
    assert_eq!(
        refusal(store.versions(SCOPE, "/facts", 50)),
        "[memory_path_invalid] Invalid memory document path '/facts': segments must be \
         [A-Za-z0-9_.-]{1,128} joined by '/'"
    );
}

#[test]
fn a_sidecar_under_another_spelling_is_a_case_collision() {
    let (home, store) = planted("fold");
    home.plant(&format!("{DIR}/versions/Facts.jsonl"), FACTS_ROWS);
    assert_eq!(
        refusal(store.versions(SCOPE, "Facts", 50)),
        "[memory_conflict] Memory conflict on 'Facts' in scope 'user:me/proj:demo': \
         case_collision"
    );
    // A scope: only a case-folding filesystem finds the other spelling.
    let folds = home.path().join("user%3AME").exists();
    match store.read("user:ME/proj:demo", "facts") {
        Err(Error::Memory(error)) if folds => assert_eq!(
            error.message,
            "Memory conflict in scope 'user:ME/proj:demo': case_collision"
        ),
        Ok(None) if !folds => {}
        _ => panic!("folds: {folds}"),
    }
}

#[cfg(unix)]
#[test]
fn a_symlink_out_of_the_root_is_refused() {
    let (home, store) = planted("escape");
    let outside = Scratch::new("escape-outside");
    outside.plant("a.md", FACTS);
    let link = home.path().join(DIR).join("documents/elsewhere");
    std::os::unix::fs::symlink(outside.path(), link).unwrap();
    assert_eq!(
        refusal(store.read(SCOPE, "elsewhere/a")),
        "[memory_path_invalid] Invalid memory document path 'elsewhere/a': escapes the store root"
    );
}

#[test]
fn every_call_holds_the_root_lock_and_leaves_its_file() {
    let home = Scratch::new("lock");
    let root = home.path().join("fresh");
    let store = FileStore::open(&root).unwrap();
    let lock_file = root.join(".store.lock");
    assert!(!lock_file.exists());
    assert!(store.read(SCOPE, "facts").unwrap().is_none());
    assert_eq!(fs::read(&lock_file).unwrap(), b"\0");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = |path: &Path| fs::metadata(path).unwrap().permissions().mode() & 0o777;
        assert_eq!((mode(&root), mode(&lock_file)), (0o700, 0o600));
    }
    // A second holder waits for the first, on the same file.
    let held = lock::acquire(&lock_file).unwrap();
    let (answered, answer) = mpsc::channel();
    let reader = thread::spawn(move || {
        let read = store
            .read(SCOPE, "facts")
            .map(|document| document.is_none());
        answered.send(read.unwrap()).unwrap();
    });
    assert!(answer.recv_timeout(Duration::from_millis(100)).is_err());
    drop(held);
    assert!(answer.recv_timeout(Duration::from_secs(10)).unwrap());
    reader.join().unwrap();
    assert_eq!(fs::read(&lock_file).unwrap(), b"\0");
}
